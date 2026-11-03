import json
import re
from typing import Any

from app.api.schemas import InterpretResponse
from app.config import settings
from app.parsers.amount_parser import parse_amount_to_kobo, verify_amount_agreement
from app.parsers.language import detect_language
from app.providers.base import BaseLLMProvider
from app.templates.speech import (
    build_balance_prompt,
    build_clarification_prompt,
    build_transfer_confirmation_prompt,
)

RE_10_PLUS_DIGITS = re.compile(r"\d{10,}")
RE_VALUE_ATTR = re.compile(r'"value"\s*:\s*"(?!\[value\])[^"]+"', re.IGNORECASE)


def _contains_10_plus_digits(obj: Any) -> bool:
    if isinstance(obj, str):
        return bool(RE_10_PLUS_DIGITS.search(obj))
    if isinstance(obj, dict):
        return any(_contains_10_plus_digits(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_10_plus_digits(item) for item in obj)
    return False


def _has_element_value(obj: Any) -> bool:
    if isinstance(obj, dict):
        if "value" in obj and obj["value"] not in (None, ""):
            return True
        return any(_has_element_value(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_element_value(item) for item in obj)
    return False


def validate_defence_in_depth(
    utterance: str,
    screen: Any = None,
    screen_descriptor: str | None = None,
    dialog_state: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Validates v1.1 defence-in-depth rules:

    1. Rejects any 10+ digit runs in utterance, screen, screen descriptor, or dialog state.
    2. Rejects any screen element containing a value.
    """
    if _contains_10_plus_digits(utterance):
        return (
            False,
            "10+ digit run detected in utterance; potential PIN, password, OTP, or account number leak",
        )

    if screen_descriptor and _contains_10_plus_digits(screen_descriptor):
        return False, "10+ digit run detected in screen descriptor"

    if screen and _contains_10_plus_digits(screen):
        return False, "10+ digit run detected in screen payload"

    if dialog_state and _contains_10_plus_digits(dialog_state):
        return False, "10+ digit run detected in dialog state"

    if screen and _has_element_value(screen):
        return (
            False,
            "Screen element contains a value; screen values must be filtered on device",
        )

    if screen_descriptor:
        try:
            parsed = json.loads(screen_descriptor)
            if _has_element_value(parsed):
                return (
                    False,
                    "Screen element contains a value; screen values must be filtered on device",
                )
        except (json.JSONDecodeError, ValueError, TypeError):
            if RE_VALUE_ATTR.search(screen_descriptor):
                return (
                    False,
                    "Screen element contains a value; screen values must be filtered on device",
                )

    return True, ""


class InterpreterService:
    def __init__(self, provider: BaseLLMProvider):
        self.provider = provider

    def _build_response(
        self,
        type: str,
        confidence: float,
        spoken_prompt: str,
        intent: str | None = None,
        params: dict[str, Any] | None = None,
        clarification_reason: str | None = None,
        language: str = "en",
    ) -> InterpretResponse:
        provider_name = settings.LLM_PROVIDER
        region = settings.LLM_REGION
        notice = (
            f"Processed by {provider_name} ({region}) pursuant to "
            f"Nigeria Data Protection Act 2023 (NDPA) cross-border guidelines."
        )
        return InterpretResponse(
            type=type,
            intent=intent,
            params=params,
            confidence=confidence,
            spoken_prompt=spoken_prompt,
            clarification_reason=clarification_reason,
            language=language,
            provider=provider_name,
            processing_region=region,
            data_processing_notice=notice,
        )

    async def interpret_utterance(
        self,
        utterance: str,
        manifest: list[dict[str, Any]],
        screen_descriptor: str | None = None,
        screen: Any = None,
        dialog_state: dict[str, Any] | None = None,
    ) -> InterpretResponse:
        """Interprets a user utterance against the allowed action manifest across 8 languages.

        Supports English, Broken English (Pidgin), Yoruba, Igbo, Hausa, French, German, and Spanish.
        Enforces v1.1 defence in depth and deterministic amount parsing.
        Never guesses on money or recipients.
        """
        # Validate defence-in-depth rules
        is_valid, reason = validate_defence_in_depth(
            utterance=utterance,
            screen=screen,
            screen_descriptor=screen_descriptor,
            dialog_state=dialog_state,
        )
        if not is_valid:
            raise ValueError(reason)

        # Detect utterance language
        lang = detect_language(utterance)

        # Ensure sanitized context: Never pass balances, beneficiary lists, passwords, or system messages
        sanitized_dialog_state = None
        if dialog_state:
            sanitized_dialog_state = {
                k: v
                for k, v in dialog_state.items()
                if k
                not in [
                    "balance",
                    "beneficiaries",
                    "account_number",
                    "pin",
                    "bvn",
                    "password",
                    "last_system_message",
                    "value",
                ]
            }

        # Resolve screen descriptor
        desc = screen_descriptor
        if not desc and screen:
            if isinstance(screen, str):
                desc = screen
            else:
                desc = json.dumps(screen)

        llm_result = await self.provider.interpret(
            utterance=utterance,
            manifest=manifest,
            screen_descriptor=desc,
            dialog_state=sanitized_dialog_state,
        )

        manifest_map = {item["name"]: item for item in manifest}

        # 1. Check confidence threshold
        if llm_result.confidence < settings.CONFIDENCE_THRESHOLD:
            prompt = build_clarification_prompt("low_confidence", lang=lang)
            return self._build_response(
                type="clarification",
                confidence=llm_result.confidence,
                spoken_prompt=prompt,
                clarification_reason="low_confidence",
                language=lang,
            )

        # 2. Check tool call count
        if len(llm_result.tool_calls) == 0:
            prompt = build_clarification_prompt("unknown_tool", lang=lang)
            return self._build_response(
                type="clarification",
                confidence=llm_result.confidence,
                spoken_prompt=prompt,
                clarification_reason="unknown_tool",
                language=lang,
            )

        if len(llm_result.tool_calls) > 1:
            prompt = build_clarification_prompt("multiple_tools", lang=lang)
            return self._build_response(
                type="clarification",
                confidence=llm_result.confidence,
                spoken_prompt=prompt,
                clarification_reason="multiple_tools",
                language=lang,
            )

        tool_call = llm_result.tool_calls[0]
        tool_name = tool_call.name
        tool_args = tool_call.arguments or {}

        # 3. Check if tool is allowed in manifest
        if tool_name not in manifest_map:
            prompt = build_clarification_prompt("unknown_tool", lang=lang)
            return self._build_response(
                type="clarification",
                confidence=llm_result.confidence,
                spoken_prompt=prompt,
                clarification_reason="unknown_tool",
                language=lang,
            )

        # 4. Handle money transfer validation
        if tool_name == "transfer_money":
            recipient = tool_args.get("recipient")
            if recipient:
                recipient = str(recipient).strip()
                # Reject placeholders
                if recipient.lower() in [
                    "someone",
                    "person",
                    "recipient",
                    "anybody",
                    "user",
                    "none",
                    "quelqu'un",
                    "jemand",
                    "alguien",
                ]:
                    recipient = None

            amount_raw = tool_args.get("amount_raw")
            if amount_raw:
                amount_raw = str(amount_raw).strip()

            # Check missing parameters
            if not recipient and not amount_raw:
                prompt = build_clarification_prompt(
                    "missing_amount_and_recipient", lang=lang
                )
                return self._build_response(
                    type="clarification",
                    intent=tool_name,
                    confidence=llm_result.confidence,
                    spoken_prompt=prompt,
                    clarification_reason="missing_amount_and_recipient",
                    language=lang,
                )

            if not recipient:
                prompt = build_clarification_prompt("missing_recipient", lang=lang)
                return self._build_response(
                    type="clarification",
                    intent=tool_name,
                    confidence=llm_result.confidence,
                    spoken_prompt=prompt,
                    clarification_reason="missing_recipient",
                    language=lang,
                )

            if not amount_raw:
                prompt = build_clarification_prompt(
                    "missing_amount", recipient=recipient, lang=lang
                )
                return self._build_response(
                    type="clarification",
                    intent=tool_name,
                    confidence=llm_result.confidence,
                    spoken_prompt=prompt,
                    clarification_reason="missing_amount",
                    language=lang,
                )

            # Deterministic parsing to kobo
            amount_kobo = parse_amount_to_kobo(amount_raw)
            if amount_kobo is None or amount_kobo <= 0:
                prompt = build_clarification_prompt("unparseable_amount", lang=lang)
                return self._build_response(
                    type="clarification",
                    intent=tool_name,
                    confidence=llm_result.confidence,
                    spoken_prompt=prompt,
                    clarification_reason="unparseable_amount",
                    language=lang,
                )

            # Cross-check against LLM's numeric estimate if provided
            llm_estimate = tool_args.get("amount_numeric_estimate")
            if llm_estimate is not None:
                try:
                    llm_num = float(llm_estimate)
                    if not verify_amount_agreement(amount_kobo, llm_num):
                        prompt = build_clarification_prompt(
                            "amount_mismatch", lang=lang
                        )
                        return self._build_response(
                            type="clarification",
                            intent=tool_name,
                            confidence=llm_result.confidence,
                            spoken_prompt=prompt,
                            clarification_reason="amount_mismatch",
                            language=lang,
                        )
                except (ValueError, TypeError):
                    prompt = build_clarification_prompt("amount_mismatch", lang=lang)
                    return self._build_response(
                        type="clarification",
                        intent=tool_name,
                        confidence=llm_result.confidence,
                        spoken_prompt=prompt,
                        clarification_reason="amount_mismatch",
                        language=lang,
                    )

            # Parameters are verified with zero guessing
            verified_params = {
                "recipient": recipient,
                "amount_raw": amount_raw,
                "amount_kobo": amount_kobo,
            }
            # Spoken prompt strictly generated from template (never from LLM)
            spoken_text = build_transfer_confirmation_prompt(
                amount_kobo, recipient, lang=lang
            )

            return self._build_response(
                type="action",
                intent=tool_name,
                params=verified_params,
                confidence=llm_result.confidence,
                spoken_prompt=spoken_text,
                language=lang,
            )

        # 5. Handle get_balance
        elif tool_name == "get_balance":
            spoken_text = build_balance_prompt(lang=lang)
            return self._build_response(
                type="action",
                intent=tool_name,
                params={},
                confidence=llm_result.confidence,
                spoken_prompt=spoken_text,
                language=lang,
            )

        # 6. Other generic manifest actions
        else:
            return self._build_response(
                type="action",
                intent=tool_name,
                params=tool_args,
                confidence=llm_result.confidence,
                spoken_prompt=f"Performing {tool_name.replace('_', ' ')}.",
                language=lang,
            )

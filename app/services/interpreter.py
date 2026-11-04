import json
import re
import uuid
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

CONTROL_COMMANDS = {
    "cancel": "cancel",
    "stop": "stop",
    "repeat": "repeat",
    "next": "next",
    "previous": "previous",
    "back": "previous",
    "help": "help",
    "describe screen": "describe_screen",
    "describe_screen": "describe_screen",
    "what's on my screen": "describe_screen",
    "what is on screen": "describe_screen",
}


def _contains_10_plus_digits(obj: Any) -> bool:
    if isinstance(obj, str):
        # Allow filtered placeholders like [number removed]
        clean_str = re.sub(r"\[number removed\]", "", obj, flags=re.IGNORECASE)
        return bool(RE_10_PLUS_DIGITS.search(clean_str))
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
        outcome: str,
        confidence: float,
        action_id: str | None = None,
        params: dict[str, Any] | None = None,
        missing_params: list[str] | None = None,
        control: str | None = None,
        message: str | None = None,
        spoken_prompt: str | None = None,
        clarification_reason: str | None = None,
        language: str = "en",
    ) -> InterpretResponse:
        provider_name = settings.LLM_PROVIDER
        region = settings.LLM_REGION
        notice = (
            f"Processed by {provider_name} ({region}) pursuant to "
            f"Nigeria Data Protection Act 2023 (NDPA) cross-border guidelines."
        )
        req_id = f"r_{uuid.uuid4().hex[:12]}"
        safe_params = params or {}
        safe_missing = missing_params or []
        prompt = spoken_prompt or message or ""

        # Map outcome to legacy type
        legacy_type = "action" if outcome == "action" else "clarification"

        return InterpretResponse(
            request_id=req_id,
            outcome=outcome,
            action_id=action_id,
            params=safe_params,
            missing_params=safe_missing,
            control=control,
            message=message,
            confidence=confidence,
            type=legacy_type,
            intent=action_id,
            spoken_prompt=prompt,
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
        """Interprets a user utterance against the allowed actions list across 8 languages.

        Conforms strictly to SDK 0.2.0 / Contract v1.1.
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

        clean_text = utterance.strip().lower()

        # Check for control commands (cancel, stop, repeat, next, previous, help, describe_screen)
        # Note: Do not return "confirm" as a control. Confirmation never goes through the model.
        if clean_text in CONTROL_COMMANDS:
            ctrl = CONTROL_COMMANDS[clean_text]
            return self._build_response(
                outcome="control",
                confidence=1.0,
                control=ctrl,
                message=f"Command {ctrl} received.",
            )

        # Detect utterance language
        lang = detect_language(utterance)

        # Build action maps
        actions_by_id = {}
        actions_by_name = {}
        for action in manifest:
            aid = action.get("id") or action.get("name")
            aname = action.get("name") or aid
            if aid:
                actions_by_id[aid] = action
            if aname:
                actions_by_name[aname] = action

        # Handle dialog continuation if pending_action_id is set
        if dialog_state and dialog_state.get("pending_action_id"):
            pending_id = dialog_state["pending_action_id"]
            if pending_id in actions_by_id:
                filled = dialog_state.get("filled_params") or {}
                # Check if this utterance supplies an amount
                parsed_kobo = parse_amount_to_kobo(utterance)
                if parsed_kobo is not None and parsed_kobo > 0:
                    merged_params = dict(filled)
                    merged_params["amount_minor"] = parsed_kobo
                    merged_params["amount_kobo"] = parsed_kobo
                    recipient = merged_params.get("recipient")
                    spoken = build_transfer_confirmation_prompt(parsed_kobo, recipient or "", lang=lang) if recipient else None
                    return self._build_response(
                        outcome="action",
                        action_id=pending_id,
                        params=merged_params,
                        confidence=0.95,
                        spoken_prompt=spoken,
                        language=lang,
                    )

        # Sanitize dialog context: Never pass balances, beneficiary lists, passwords, or system messages
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

        # 1. Check confidence threshold
        if llm_result.confidence < settings.CONFIDENCE_THRESHOLD:
            msg = build_clarification_prompt("low_confidence", lang=lang)
            return self._build_response(
                outcome="clarify",
                confidence=llm_result.confidence,
                message=msg,
                spoken_prompt=msg,
                clarification_reason="low_confidence",
                language=lang,
            )

        # 2. Check tool call count
        if len(llm_result.tool_calls) == 0:
            msg = build_clarification_prompt("unknown_tool", lang=lang)
            return self._build_response(
                outcome="unsupported",
                confidence=llm_result.confidence,
                message=msg,
                spoken_prompt=msg,
                clarification_reason="unknown_tool",
                language=lang,
            )

        if len(llm_result.tool_calls) > 1:
            msg = build_clarification_prompt("multiple_tools", lang=lang)
            return self._build_response(
                outcome="clarify",
                confidence=llm_result.confidence,
                message=msg,
                spoken_prompt=msg,
                clarification_reason="multiple_tools",
                language=lang,
            )

        tool_call = llm_result.tool_calls[0]
        tool_name = tool_call.name
        tool_args = tool_call.arguments or {}

        # 3. Check if tool is allowed in actions
        action_match = actions_by_id.get(tool_name) or actions_by_name.get(tool_name)
        if not action_match:
            msg = build_clarification_prompt("unknown_tool", lang=lang)
            return self._build_response(
                outcome="unsupported",
                confidence=llm_result.confidence,
                message=msg,
                spoken_prompt=msg,
                clarification_reason="unknown_tool",
                language=lang,
            )

        canonical_action_id = action_match.get("id") or action_match.get("name")

        # 4. Handle money transfer validation
        if canonical_action_id == "transfer_money":
            recipient = tool_args.get("recipient")
            if recipient:
                recipient = str(recipient).strip()
                # Reject placeholders
                if recipient.lower() in [
                    "someone", "person", "recipient", "anybody", "user",
                    "none", "quelqu'un", "jemand", "alguien",
                ]:
                    recipient = None

            amount_raw = tool_args.get("amount_raw")
            amount_minor_in = tool_args.get("amount_minor")
            if amount_raw:
                amount_raw = str(amount_raw).strip()

            # Check missing parameters
            if not recipient and not amount_raw and not amount_minor_in:
                msg = build_clarification_prompt("missing_amount_and_recipient", lang=lang)
                return self._build_response(
                    outcome="clarify",
                    action_id=canonical_action_id,
                    missing_params=["recipient", "amount_minor"],
                    confidence=llm_result.confidence,
                    message=msg,
                    spoken_prompt=msg,
                    clarification_reason="missing_amount_and_recipient",
                    language=lang,
                )

            if not recipient:
                msg = build_clarification_prompt("missing_recipient", lang=lang)
                params_heard = {}
                if amount_raw:
                    kobo_val = parse_amount_to_kobo(amount_raw)
                    if kobo_val is not None:
                        params_heard["amount_minor"] = kobo_val
                        params_heard["amount_kobo"] = kobo_val
                return self._build_response(
                    outcome="clarify",
                    action_id=canonical_action_id,
                    params=params_heard,
                    missing_params=["recipient"],
                    confidence=llm_result.confidence,
                    message=msg,
                    spoken_prompt=msg,
                    clarification_reason="missing_recipient",
                    language=lang,
                )

            if not amount_raw and not amount_minor_in:
                msg = build_clarification_prompt("missing_amount", recipient=recipient, lang=lang)
                return self._build_response(
                    outcome="clarify",
                    action_id=canonical_action_id,
                    params={"recipient": recipient},
                    missing_params=["amount_minor"],
                    confidence=llm_result.confidence,
                    message=msg,
                    spoken_prompt=msg,
                    clarification_reason="missing_amount",
                    language=lang,
                )

            # Deterministic parsing to kobo
            amount_kobo = None
            if amount_raw:
                amount_kobo = parse_amount_to_kobo(amount_raw)
            elif amount_minor_in is not None:
                try:
                    amount_kobo = int(amount_minor_in)
                except (ValueError, TypeError):
                    amount_kobo = None

            if amount_kobo is None or amount_kobo <= 0:
                msg = build_clarification_prompt("unparseable_amount", lang=lang)
                return self._build_response(
                    outcome="clarify",
                    action_id=canonical_action_id,
                    params={"recipient": recipient},
                    missing_params=["amount_minor"],
                    confidence=llm_result.confidence,
                    message=msg,
                    spoken_prompt=msg,
                    clarification_reason="unparseable_amount",
                    language=lang,
                )

            # Cross-check against LLM's numeric estimate if provided
            llm_estimate = tool_args.get("amount_numeric_estimate")
            if llm_estimate is not None:
                try:
                    llm_num = float(llm_estimate)
                    if not verify_amount_agreement(amount_kobo, llm_num):
                        msg = build_clarification_prompt("amount_mismatch", lang=lang)
                        return self._build_response(
                            outcome="clarify",
                            action_id=canonical_action_id,
                            params={"recipient": recipient},
                            missing_params=["amount_minor"],
                            confidence=llm_result.confidence,
                            message=msg,
                            spoken_prompt=msg,
                            clarification_reason="amount_mismatch",
                            language=lang,
                        )
                except (ValueError, TypeError):
                    pass

            # Valid parameters with amount_minor as JSON integer in kobo
            verified_params = {
                "recipient": recipient,
                "amount_minor": amount_kobo,
                # Backward-compatibility alias
                "amount_kobo": amount_kobo,
            }
            # Spoken prompt strictly generated from static template (never from LLM)
            spoken_text = build_transfer_confirmation_prompt(
                amount_kobo, recipient, lang=lang
            )

            return self._build_response(
                outcome="action",
                action_id=canonical_action_id,
                params=verified_params,
                missing_params=[],
                confidence=llm_result.confidence,
                spoken_prompt=spoken_text,
                language=lang,
            )

        # 5. Handle get_balance
        elif canonical_action_id == "get_balance":
            spoken_text = build_balance_prompt(lang=lang)
            return self._build_response(
                outcome="action",
                action_id=canonical_action_id,
                params={},
                missing_params=[],
                confidence=llm_result.confidence,
                spoken_prompt=spoken_text,
                language=lang,
            )

        # 6. Other generic manifest actions
        else:
            return self._build_response(
                outcome="action",
                action_id=canonical_action_id,
                params=tool_args,
                missing_params=[],
                confidence=llm_result.confidence,
                spoken_prompt=f"Performing {canonical_action_id.replace('_', ' ')}.",
                language=lang,
            )

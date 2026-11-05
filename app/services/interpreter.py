import json
import re
import uuid
from typing import Any

from app.api.schemas import InterpretResponse
from app.config import settings
from app.parsers.amount_parser import parse_amount_to_kobo
from app.parsers.language import detect_language
from app.providers.base import BaseLLMProvider
from app.templates.speech import (
    build_balance_prompt,
    build_clarification_prompt,
    build_transactions_prompt,
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

        # Map outcome to legacy type: if there are missing params, legacy clients see "clarification",
        # while v1.1 contract sees outcome="action" with missing_params populated.
        legacy_type = (
            "clarification"
            if safe_missing
            else ("action" if outcome == "action" else "clarification")
        )

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
        locale: str | None = None,
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

        # Detect utterance language with optional client locale hint
        lang = detect_language(utterance, locale=locale)

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
                    spoken = (
                        build_transfer_confirmation_prompt(
                            parsed_kobo, recipient or "", lang=lang
                        )
                        if recipient
                        else None
                    )
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

        canonical_action_id = (
            action_match.get("id") or action_match.get("name") or tool_name
        )

        # 4. Handle get_balance
        if canonical_action_id in ("get_balance", "check_balance", "balance"):
            spoken_text = build_balance_prompt(lang=lang)
            return self._build_response(
                outcome="action",
                action_id=canonical_action_id,
                params={},
                missing_params=[],
                confidence=llm_result.confidence,
                message=None,
                spoken_prompt=spoken_text,
                language=lang,
            )

        # 5. Handle get_transactions / history
        if canonical_action_id in ("get_transactions", "get_transaction_history"):
            spoken_text = build_transactions_prompt(lang=lang)
            return self._build_response(
                outcome="action",
                action_id=canonical_action_id,
                params={},
                missing_params=[],
                confidence=llm_result.confidence,
                message=None,
                spoken_prompt=spoken_text,
                language=lang,
            )

        # 6. Manifest-driven parameter extraction and grounding for all actions
        # (Fixes H2, bill payments, and partial requests)
        manifest_params = action_match.get("params")
        if not manifest_params and isinstance(action_match.get("parameters"), dict):
            props = action_match["parameters"].get("properties", {})
            req_set = set(action_match["parameters"].get("required") or [])
            manifest_params = [
                {"name": k, "type": v.get("type", "string"), "required": k in req_set}
                for k, v in props.items()
            ]

        declared_params: dict[str, dict[str, Any]] = {}
        required_param_names: list[str] = []
        amount_param_names: list[str] = []
        recipient_param_name: str | None = None

        if isinstance(manifest_params, list):
            for p in manifest_params:
                if isinstance(p, dict):
                    p_name = p.get("name")
                    if not p_name:
                        continue
                    declared_params[p_name] = p
                    if p.get("required"):
                        required_param_names.append(p_name)

                    p_type = str(p.get("type", "")).lower()
                    p_lower = p_name.lower()
                    if (
                        p_type == "amount_minor"
                        or "amount" in p_lower
                        or p_lower in ("kobo", "sum")
                    ):
                        amount_param_names.append(p_name)
                    elif "recipient" in p_lower or "beneficiary" in p_lower:
                        recipient_param_name = p_name

        verified_params: dict[str, Any] = {}

        # 6a. Ground and parse amount (H2: Never trust hallucinated amounts)
        if amount_param_names:
            # Deterministic parse strictly from user utterance
            parsed_from_utt = parse_amount_to_kobo(utterance)
            raw_model_val = tool_args.get("amount_raw")
            # Only consider amount_raw if it is actually substring in user utterance
            if raw_model_val and str(raw_model_val).lower() in utterance.lower():
                parsed_from_raw = parse_amount_to_kobo(str(raw_model_val))
                amount_kobo = parsed_from_raw or parsed_from_utt
            else:
                amount_kobo = parsed_from_utt

            if amount_kobo is not None and amount_kobo > 0:
                verified_params["amount_minor"] = amount_kobo
                verified_params["amount_kobo"] = amount_kobo
                for p_name in amount_param_names:
                    p_def = declared_params[p_name]
                    p_type = str(p_def.get("type", "")).lower()
                    if p_name == "amount_raw" or p_type == "string":
                        if (
                            raw_model_val
                            and str(raw_model_val).lower() in utterance.lower()
                        ):
                            verified_params[p_name] = str(raw_model_val)
                        else:
                            verified_params[p_name] = f"{int(amount_kobo / 100)}"
                    elif p_name == "amount_numeric_estimate" or p_type in (
                        "number",
                        "float",
                    ):
                        verified_params[p_name] = float(amount_kobo / 100)
                    else:
                        verified_params[p_name] = amount_kobo

        # 6b. Ground recipient in utterance (H2: Never trust invented recipients)
        if recipient_param_name:
            cand_recip = (
                tool_args.get(recipient_param_name)
                or tool_args.get("recipient")
                or tool_args.get("beneficiary")
            )
            if cand_recip:
                cand_str = str(cand_recip).strip()
                # Grounding check: recipient MUST be present in user utterance and not a placeholder!
                if (
                    cand_str.lower()
                    not in [
                        "someone",
                        "person",
                        "recipient",
                        "anybody",
                        "user",
                        "none",
                        "quelqu'un",
                        "jemand",
                        "alguien",
                    ]
                    and cand_str.lower() in utterance.lower()
                ):
                    verified_params[recipient_param_name] = cand_str
                    verified_params["recipient"] = cand_str

        # 6c. Ground any other declared params from tool_args
        for p_name in declared_params:
            if p_name in verified_params:
                continue
            # Never copy ungrounded amount or recipient params from tool_args (H2 fix)
            p_lower = p_name.lower()
            if (
                "amount" in p_lower
                or p_lower in ("kobo", "sum")
                or "recipient" in p_lower
                or "beneficiary" in p_lower
            ):
                continue
            val = tool_args.get(p_name)
            if val is not None and (
                not isinstance(val, str)
                or (isinstance(val, str) and val.lower() in utterance.lower())
            ):
                verified_params[p_name] = val

        # 6d. Compute missing required parameters
        missing_params = [
            p_name for p_name in required_param_names if p_name not in verified_params
        ]

        # 6e. If required parameters are missing, outcome remains "action" with missing_params list
        # (Contract v1.1 / SDK DialogEngine standard)
        if missing_params:
            has_missing_recip = bool(
                recipient_param_name and recipient_param_name in missing_params
            )
            has_missing_amt = any(p in missing_params for p in amount_param_names)

            if has_missing_recip and has_missing_amt:
                prompt = build_clarification_prompt(
                    "missing_amount_and_recipient", lang=lang
                )
                c_reason = "missing_amount_and_recipient"
            elif has_missing_recip:
                prompt = build_clarification_prompt("missing_recipient", lang=lang)
                c_reason = "missing_recipient"
            elif has_missing_amt:
                c_reason = "missing_amount"
                recip_val = verified_params.get("recipient")
                if recip_val:
                    prompt = build_clarification_prompt(
                        "missing_amount", recipient=recip_val, lang=lang
                    )
                else:
                    prompts = {
                        "en": "How much would you like to pay?",
                        "pcm": "How much you wan pay?",
                        "yo": "Iye owo melo ni ẹ fẹ san?",
                        "ig": "Ego ole ka ị chọrọ ịkwụ?",
                        "ha": "Nawa kake son biya?",
                        "fr": "Combien souhaitez-vous payer ?",
                        "de": "Wie viel möchten Sie bezahlen?",
                        "es": "¿Cuánto le gustaría pagar?",
                    }
                    prompt = prompts.get(lang, prompts["en"])
            else:
                prompt = f"Please specify {', '.join(missing_params)}."
                c_reason = f"missing_{'_and_'.join(missing_params)}"

            return self._build_response(
                outcome="action",
                action_id=canonical_action_id,
                params=verified_params,
                missing_params=missing_params,
                confidence=llm_result.confidence,
                message=prompt,
                spoken_prompt=prompt,
                clarification_reason=c_reason,
                language=lang,
            )

        # 6f. All required parameters are present: Action is complete
        amount_kobo = (
            verified_params.get("amount_minor")
            or verified_params.get("amount_kobo")
            or 0
        )
        recip_val = verified_params.get("recipient")
        if recip_val and amount_kobo:
            spoken_text = build_transfer_confirmation_prompt(
                int(amount_kobo), str(recip_val), lang=lang
            )
        elif amount_kobo:
            naira = int(amount_kobo) / 100
            spoken_text = f"Pay {naira:,.0f} Naira for {canonical_action_id.replace('_', ' ')}. Say 'confirm' to proceed, or 'cancel' to abort."
        else:
            spoken_text = f"Please confirm {canonical_action_id.replace('_', ' ')}."

        return self._build_response(
            outcome="action",
            action_id=canonical_action_id,
            params=verified_params,
            missing_params=[],
            confidence=llm_result.confidence,
            message=None,
            spoken_prompt=spoken_text,
            language=lang,
        )

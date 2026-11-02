from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from app.config import settings
from app.providers.base import BaseLLMProvider
from app.parsers.amount_parser import parse_amount_to_kobo, verify_amount_agreement
from app.templates.speech import (
    build_transfer_confirmation_prompt,
    build_clarification_prompt
)

class InterpretResponse(BaseModel):
    type: str  # "action" or "clarification"
    intent: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    confidence: float
    spoken_prompt: str
    clarification_reason: Optional[str] = None

class InterpreterService:
    def __init__(self, provider: BaseLLMProvider):
        self.provider = provider

    async def interpret_utterance(
        self,
        utterance: str,
        manifest: List[Dict[str, Any]],
        screen_descriptor: Optional[str] = None,
        dialog_state: Optional[Dict[str, Any]] = None
    ) -> InterpretResponse:
        """
        Interprets a user utterance against the allowed action manifest.
        Applies deterministic amount parsing and strict safety verification.
        Never guesses on money or recipients.
        """
        # Ensure sanitized context: Only utterance, manifest, screen descriptor, and sanitized dialog state
        # Never pass balances or beneficiary lists
        sanitized_dialog_state = None
        if dialog_state:
            sanitized_dialog_state = {
                k: v for k, v in dialog_state.items()
                if k not in ["balance", "beneficiaries", "account_number", "pin", "bvn", "password"]
            }

        llm_result = await self.provider.interpret(
            utterance=utterance,
            manifest=manifest,
            screen_descriptor=screen_descriptor,
            dialog_state=sanitized_dialog_state
        )

        manifest_map = {item["name"]: item for item in manifest}

        # 1. Check confidence threshold
        if llm_result.confidence < settings.CONFIDENCE_THRESHOLD:
            prompt = build_clarification_prompt("low_confidence")
            return InterpretResponse(
                type="clarification",
                confidence=llm_result.confidence,
                spoken_prompt=prompt,
                clarification_reason="low_confidence"
            )

        # 2. Check tool call count
        if len(llm_result.tool_calls) == 0:
            prompt = build_clarification_prompt("unknown_tool")
            return InterpretResponse(
                type="clarification",
                confidence=llm_result.confidence,
                spoken_prompt=prompt,
                clarification_reason="unknown_tool"
            )

        if len(llm_result.tool_calls) > 1:
            prompt = build_clarification_prompt("multiple_tools")
            return InterpretResponse(
                type="clarification",
                confidence=llm_result.confidence,
                spoken_prompt=prompt,
                clarification_reason="multiple_tools"
            )

        tool_call = llm_result.tool_calls[0]
        tool_name = tool_call.name
        tool_args = tool_call.arguments or {}

        # 3. Check if tool is allowed in manifest
        if tool_name not in manifest_map:
            prompt = build_clarification_prompt("unknown_tool")
            return InterpretResponse(
                type="clarification",
                confidence=llm_result.confidence,
                spoken_prompt=prompt,
                clarification_reason="unknown_tool"
            )

        # 4. Handle money transfer validation
        if tool_name == "transfer_money":
            recipient = tool_args.get("recipient")
            if recipient:
                recipient = str(recipient).strip()
                # Reject placeholders
                if recipient.lower() in ["someone", "person", "recipient", "anybody", "user", "none"]:
                    recipient = None
                    
            amount_raw = tool_args.get("amount_raw")
            if amount_raw:
                amount_raw = str(amount_raw).strip()

            # Check missing parameters
            if not recipient and not amount_raw:
                prompt = build_clarification_prompt("missing_amount_and_recipient")
                return InterpretResponse(
                    type="clarification",
                    intent=tool_name,
                    confidence=llm_result.confidence,
                    spoken_prompt=prompt,
                    clarification_reason="missing_amount_and_recipient"
                )

            if not recipient:
                prompt = build_clarification_prompt("missing_recipient")
                return InterpretResponse(
                    type="clarification",
                    intent=tool_name,
                    confidence=llm_result.confidence,
                    spoken_prompt=prompt,
                    clarification_reason="missing_recipient"
                )

            if not amount_raw:
                prompt = build_clarification_prompt("missing_amount", recipient=recipient)
                return InterpretResponse(
                    type="clarification",
                    intent=tool_name,
                    confidence=llm_result.confidence,
                    spoken_prompt=prompt,
                    clarification_reason="missing_amount"
                )

            # Deterministic parsing to kobo
            amount_kobo = parse_amount_to_kobo(amount_raw)
            if amount_kobo is None or amount_kobo <= 0:
                prompt = build_clarification_prompt("unparseable_amount")
                return InterpretResponse(
                    type="clarification",
                    intent=tool_name,
                    confidence=llm_result.confidence,
                    spoken_prompt=prompt,
                    clarification_reason="unparseable_amount"
                )

            # Cross-check against LLM's numeric estimate if provided
            llm_estimate = tool_args.get("amount_numeric_estimate")
            if llm_estimate is not None:
                try:
                    llm_num = float(llm_estimate)
                    if not verify_amount_agreement(amount_kobo, llm_num):
                        prompt = build_clarification_prompt("amount_mismatch")
                        return InterpretResponse(
                            type="clarification",
                            intent=tool_name,
                            confidence=llm_result.confidence,
                            spoken_prompt=prompt,
                            clarification_reason="amount_mismatch"
                        )
                except (ValueError, TypeError):
                    prompt = build_clarification_prompt("amount_mismatch")
                    return InterpretResponse(
                        type="clarification",
                        intent=tool_name,
                        confidence=llm_result.confidence,
                        spoken_prompt=prompt,
                        clarification_reason="amount_mismatch"
                    )

            # Parameters are verified with zero guessing
            verified_params = {
                "recipient": recipient,
                "amount_raw": amount_raw,
                "amount_kobo": amount_kobo
            }
            # Spoken prompt strictly generated from template (never from LLM)
            spoken_text = build_transfer_confirmation_prompt(amount_kobo, recipient)

            return InterpretResponse(
                type="action",
                intent=tool_name,
                params=verified_params,
                confidence=llm_result.confidence,
                spoken_prompt=spoken_text
            )

        # 5. Handle get_balance
        elif tool_name == "get_balance":
            return InterpretResponse(
                type="action",
                intent=tool_name,
                params={},
                confidence=llm_result.confidence,
                spoken_prompt="Checking your account balance."
            )

        # 6. Other generic manifest actions
        else:
            return InterpretResponse(
                type="action",
                intent=tool_name,
                params=tool_args,
                confidence=llm_result.confidence,
                spoken_prompt=f"Performing {tool_name.replace('_', ' ')}."
            )

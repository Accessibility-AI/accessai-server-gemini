import re
from typing import Any, Dict, List, Optional
from app.providers.base import BaseLLMProvider, LLMInterpretationResult, ToolCall
from app.parsers.amount_parser import parse_amount_to_kobo

class MockLLMProvider(BaseLLMProvider):
    """
    Deterministic rule-based provider for evaluations, test suites, and offline mode.
    Emulates LLM tool-calling with high precision for Nigerian English and Pidgin.
    """
    async def interpret(
        self,
        utterance: str,
        manifest: List[Dict[str, Any]],
        screen_descriptor: Optional[str] = None,
        dialog_state: Optional[Dict[str, Any]] = None
    ) -> LLMInterpretationResult:
        manifest_names = {item["name"] for item in manifest}
        text = utterance.strip().lower()

        # 1. Check for balance inquiries
        balance_patterns = [
            r"\b(balance|wetin be my balance|how much dey|check my account|check account|show balance|view balance|account balance|how much i get|how much remain|how much is in|how much is left|what do i have in my account|check my money|wetin remain|how much money dey)\b"
        ]
        for pat in balance_patterns:
            if re.search(pat, text):
                if "get_balance" in manifest_names:
                    return LLMInterpretationResult(
                        tool_calls=[ToolCall(name="get_balance", arguments={})],
                        confidence=0.96
                    )

        # 2. Check for transfer requests
        transfer_keywords = [
            r"\b(send|transfer|wire|pay|give)\b"
        ]
        is_transfer = any(re.search(pat, text) for pat in transfer_keywords)

        if is_transfer and "transfer_money" in manifest_names:
            args: Dict[str, Any] = {}
            
            # Pattern matching for amount extraction
            amount_patterns = [
                # Compound naira and kobo e.g. 500 naira 50 kobo
                r"(\d+\s*naira\s*(?:and\s*)?\d+\s*kobo)",
                # Special fractions like half a million
                r"\b(half\s+(?:a\s+)?(?:million|billion|thousand))\b(?:\s*naira)?",
                # Shorthand notation like 5k, 1.5k, 20 grand, 2m, etc.
                r"(?:₦|n|ngn|#)?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(?:k|grand|m|million|b|billion)\b(?:\s*naira)?",
                # Currency symbols like N5000, ₦5,000, #5000
                r"(?:₦|n|ngn|#)\s*(\d+(?:,\d+)*(?:\.\d+)?)",
                # Digits with naira / kobo
                r"\b(\d+(?:,\d+)*(?:\.\d+)?)\s*(?:naira|kobo)\b",
                # Written out words e.g. twenty thousand naira
                r"\b((?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|\s|-)+)\s*(?:naira|kobo)\b"
            ]
            
            raw_amount_str = None
            for pat in amount_patterns:
                m = re.search(pat, text)
                if m:
                    candidate = m.group(0).strip()
                    # Filter out purely non-number words
                    kobo_test = parse_amount_to_kobo(candidate)
                    if kobo_test is not None and kobo_test > 0:
                        raw_amount_str = candidate
                        break

            if raw_amount_str:
                args["amount_raw"] = raw_amount_str
                kobo_val = parse_amount_to_kobo(raw_amount_str)
                if kobo_val is not None:
                    args["amount_numeric_estimate"] = float(kobo_val / 100)

            # Recipient extraction
            # e.g., "give David", "to Chinedu", "wire 5k to Amaka", "send 5k give David", "transfer to Sarah"
            recip_patterns = [
                r"\b(?:to|give|dash)\s+([a-zA-Z]+)(?:\s|$)",
                r"\b(?:send|transfer|wire|pay)\s+([a-zA-Z]+)\s+(?:₦|n|\d+|some|money)",
            ]
            recipient = None
            for pat in recip_patterns:
                m = re.search(pat, text)
                if m:
                    cand = m.group(1).strip()
                    # Ignore common stopwords/action words
                    if cand.lower() not in ["money", "cash", "naira", "kobo", "balance", "him", "her", "them", "me", "my", "account", "the"]:
                        recipient = cand.capitalize()
                        break
                        
            if recipient:
                args["recipient"] = recipient

            return LLMInterpretationResult(
                tool_calls=[ToolCall(name="transfer_money", arguments=args)],
                confidence=0.95
            )

        # 3. Check for multiple actions or unhandled
        if re.search(r"\b(transfer|send).*(?:and|then).*(?:balance|transfer|send)\b", text):
            # Multiple tools requested
            return LLMInterpretationResult(
                tool_calls=[
                    ToolCall(name="transfer_money", arguments={}),
                    ToolCall(name="get_balance", arguments={})
                ],
                confidence=0.9
            )

        # 4. Unknown tool or completely out of banking scope
        return LLMInterpretationResult(
            tool_calls=[],
            confidence=0.3,
            text_content="I could not identify a valid banking action for your request."
        )

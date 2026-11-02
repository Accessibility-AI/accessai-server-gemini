from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]


class LLMInterpretationResult(BaseModel):
    tool_calls: list[ToolCall] = []
    confidence: float = 1.0
    text_content: str | None = None
    raw_response: Any | None = None


class BaseLLMProvider(ABC):
    @abstractmethod
    async def interpret(
        self,
        utterance: str,
        manifest: list[dict[str, Any]],
        screen_descriptor: str | None = None,
        dialog_state: dict[str, Any] | None = None,
    ) -> LLMInterpretationResult:
        """
        Takes utterance, action manifest, screen descriptor, and dialog state.
        Never receives PII, balances, or beneficiary lists.
        Exposes manifest actions as tools and returns tool calls.
        """

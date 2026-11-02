from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

class ToolCall(BaseModel):
    name: str
    arguments: Dict[str, Any]

class LLMInterpretationResult(BaseModel):
    tool_calls: List[ToolCall] = []
    confidence: float = 1.0
    text_content: Optional[str] = None
    raw_response: Optional[Any] = None

class BaseLLMProvider(ABC):
    @abstractmethod
    async def interpret(
        self,
        utterance: str,
        manifest: List[Dict[str, Any]],
        screen_descriptor: Optional[str] = None,
        dialog_state: Optional[Dict[str, Any]] = None
    ) -> LLMInterpretationResult:
        """
        Takes utterance, action manifest, screen descriptor, and dialog state.
        Never receives PII, balances, or beneficiary lists.
        Exposes manifest actions as tools and returns tool calls.
        """
        pass

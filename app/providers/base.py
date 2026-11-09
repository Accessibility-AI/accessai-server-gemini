import re
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

_INVALID_TOOL_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_TOOL_NAME_LEN = 64

# Contract v1.1 param types -> JSON Schema types
_PARAM_JSON_TYPES = {
  "amount_minor": "integer",
  "integer": "integer",
  "int": "integer",
  "number": "number",
  "float": "number",
  "boolean": "boolean",
  "string": "string",
}


def sanitize_tool_name(raw: str) -> str:
  """Makes an action id safe as an LLM tool name (^[a-zA-Z0-9_-]{1,64}$).

  Groq/OpenAI, Anthropic and Gemini all reject names with spaces, e.g. the SDK's
  human-readable "send money". The interpreter maps sanitized names back to actions.
  """
  name = _INVALID_TOOL_NAME_CHARS.sub("_", (raw or "").strip())
  return name[:_MAX_TOOL_NAME_LEN] or "action"


def manifest_to_tool_schema(
  action: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
  """Converts one manifest action into (tool_name, description, JSON Schema parameters).

  Accepts both the SDK contract v1.1 shape (`id` + `params` list) and the native
  shape (`name` + JSON Schema `parameters`). Shared by every provider so they all
  expose the same tools for the same manifest.

  Actions with an amount param also get `amount_raw`, so the model can return the
  amount exactly as spoken; the interpreter then parses it deterministically.
  """
  raw_id = action.get("id") or action.get("name") or "action"
  tool_name = sanitize_tool_name(str(raw_id))
  description = action.get("description") or action.get("name") or ""

  if isinstance(action.get("parameters"), dict):
    return tool_name, description, action["parameters"]

  params = action.get("params")
  if not isinstance(params, list):
    return tool_name, description, {"type": "object", "properties": {}}

  properties: dict[str, Any] = {}
  required: list[str] = []
  has_amount = False
  for p in params:
    if not isinstance(p, dict) or not p.get("name"):
      continue
    p_name = p["name"]
    p_type = str(p.get("type", "string")).lower()
    properties[p_name] = {
      "type": _PARAM_JSON_TYPES.get(p_type, "string"),
      "description": p.get("description") or f"Parameter {p_name}",
    }
    if p.get("required"):
      required.append(p_name)
    if p_type == "amount_minor" or "amount" in p_name.lower():
      has_amount = True

  if has_amount and "amount_raw" not in properties:
    properties["amount_raw"] = {
      "type": "string",
      "description": (
        "The amount exactly as the user said it (e.g. '5k', 'five thousand naira'). "
        "Leave empty if the user did not say an amount."
      ),
    }

  return (
    tool_name,
    description,
    {"type": "object", "properties": properties, "required": required},
  )


class ProviderRateLimitedError(Exception):
  """The LLM provider refused the request because a quota or rate limit was hit.

  Mapped to `429 rate_limited` for clients. The provider's own error body is logged,
  never returned.
  """

  def __init__(self, provider: str, retry_after: int | None = None):
    self.provider = provider
    self.retry_after = retry_after
    super().__init__(f"{provider} rate limit reached")


def retry_after_seconds(value: Any) -> int | None:
  """Parses a Retry-After header value or a Google retryDelay such as '55s'."""
  if value is None:
    return None
  text = str(value).strip().removesuffix("s")
  try:
    return max(1, round(float(text)))
  except ValueError:
    return None


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

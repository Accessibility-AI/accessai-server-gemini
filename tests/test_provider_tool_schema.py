"""Every provider must expose the same, API-valid tools for the same manifest."""

from types import SimpleNamespace

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import (
  BaseLLMProvider,
  LLMInterpretationResult,
  ToolCall,
  manifest_to_tool_schema,
  sanitize_tool_name,
)
from app.providers.gemini_provider import action_to_gemini_declaration
from app.providers.groq_provider import GroqProvider
from app.services.interpreter import InterpreterService

SDK_TRANSFER = {
  "id": "transfer_money",
  "name": "send money",
  "description": "Send money to a saved beneficiary",
  "sensitivity": "sensitive",
  "requires_confirmation": True,
  "params": [
    {"name": "recipient", "type": "string", "required": True},
    {"name": "amount_minor", "type": "amount_minor", "required": True},
  ],
}

# A manifest action identified only by a human-readable name
NAME_ONLY_TRANSFER = {
  "name": "send money",
  "description": "Send money",
  "params": SDK_TRANSFER["params"],
}


def test_sanitize_tool_name():
  assert sanitize_tool_name("transfer_money") == "transfer_money"
  assert sanitize_tool_name("send money") == "send_money"
  assert sanitize_tool_name(" pay.bill/now ") == "pay_bill_now"
  assert sanitize_tool_name("") == "action"
  assert len(sanitize_tool_name("x" * 100)) == 64


def test_sdk_params_list_becomes_json_schema_with_amount_raw():
  name, description, schema = manifest_to_tool_schema(SDK_TRANSFER)
  assert name == "transfer_money"
  assert description == "Send money to a saved beneficiary"
  assert schema["type"] == "object"
  assert schema["properties"]["recipient"]["type"] == "string"
  assert schema["properties"]["amount_minor"]["type"] == "integer"
  assert schema["properties"]["amount_raw"]["type"] == "string"
  assert schema["required"] == ["recipient", "amount_minor"]


def test_native_parameters_pass_through_unchanged():
  native = {
    "name": "transfer_money",
    "parameters": {"type": "object", "properties": {"recipient": {"type": "string"}}},
  }
  name, _, schema = manifest_to_tool_schema(native)
  assert name == "transfer_money"
  assert schema is native["parameters"]


def test_action_without_params_gets_empty_object_schema():
  name, _, schema = manifest_to_tool_schema({"id": "get_balance", "params": []})
  assert name == "get_balance"
  assert schema == {"type": "object", "properties": {}, "required": []}


def test_gemini_declaration_uses_shared_schema():
  decl = action_to_gemini_declaration(NAME_ONLY_TRANSFER)
  assert decl["name"] == "send_money"
  assert decl["parameters"]["type"] == "OBJECT"
  assert decl["parameters"]["properties"]["amount_minor"]["type"] == "INTEGER"
  assert decl["parameters"]["properties"]["amount_raw"]["type"] == "STRING"


async def test_groq_sends_sanitized_tools_built_from_params():
  captured = {}

  async def create(**kwargs):
    captured.update(kwargs)
    message = SimpleNamespace(tool_calls=None, content="")
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])

  provider = GroqProvider(api_key="test-key")
  provider.client = SimpleNamespace(
    chat=SimpleNamespace(completions=SimpleNamespace(create=create))
  )
  await provider.interpret("send 5k to David", [NAME_ONLY_TRANSFER])

  fn = captured["tools"][0]["function"]
  assert fn["name"] == "send_money"
  assert set(fn["parameters"]["properties"]) == {
    "recipient",
    "amount_minor",
    "amount_raw",
  }


async def test_anthropic_sends_sanitized_tools_built_from_params():
  captured = {}

  async def create(**kwargs):
    captured.update(kwargs)
    return SimpleNamespace(content=[])

  provider = AnthropicProvider(api_key="test-key")
  provider.client = SimpleNamespace(messages=SimpleNamespace(create=create))
  await provider.interpret("send 5k to David", [NAME_ONLY_TRANSFER])

  tool = captured["tools"][0]
  assert tool["name"] == "send_money"
  assert set(tool["input_schema"]["properties"]) == {
    "recipient",
    "amount_minor",
    "amount_raw",
  }


class _StubProvider(BaseLLMProvider):
  def __init__(self, tool_call: ToolCall):
    self.tool_call = tool_call

  async def interpret(
    self, utterance, manifest, screen_descriptor=None, dialog_state=None
  ):
    return LLMInterpretationResult(tool_calls=[self.tool_call], confidence=0.95)


async def test_interpreter_maps_sanitized_tool_name_back_to_action():
  provider = _StubProvider(
    ToolCall(name="send_money", arguments={"recipient": "David", "amount_raw": "5k"})
  )
  result = await InterpreterService(provider).interpret_utterance(
    utterance="send 5k to David", manifest=[NAME_ONLY_TRANSFER]
  )
  assert result.outcome == "action"
  assert result.action_id == "send money"
  assert result.params["amount_minor"] == 500_000
  assert result.params["recipient"] == "David"

"""Interpret returns only manifest-declared params, and spoken prompts still read back
the amount and recipient whatever names the manifest uses (#30)."""

import pytest

from app.providers.mock_provider import MockLLMProvider
from app.services.confirmation import spoken_amount_and_recipient
from app.services.interpreter import InterpreterService, ParamRoles
from app.templates.speech import build_action_confirmed_prompt

CUSTOM_NAMES_MANIFEST = [
  {
    "id": "send_money",
    "name": "send money",
    "params": [
      {"name": "beneficiary", "type": "string", "required": True},
      {"name": "amount", "type": "amount_minor", "required": True},
    ],
  }
]

LEGACY_MANIFEST = [
  {
    "name": "transfer_money",
    "parameters": {
      "type": "object",
      "properties": {
        "amount_raw": {"type": "string"},
        "amount_numeric_estimate": {"type": "number"},
        "recipient": {"type": "string"},
      },
      "required": ["amount_raw", "recipient"],
    },
  }
]

BILL_ACTION = {
  "id": "pay_bill",
  "name": "pay bill",
  "params": [{"name": "amount_minor", "type": "amount_minor", "required": True}],
}

NO_AMOUNT_ACTION = {
  "id": "add_beneficiary",
  "name": "add beneficiary",
  "params": [{"name": "recipient", "type": "string", "required": True}],
}

NO_PARAMS_ACTION = {"id": "freeze_card", "name": "freeze card", "params": []}


@pytest.fixture
def service():
  return InterpreterService(provider=MockLLMProvider())


@pytest.mark.asyncio
async def test_custom_param_names_still_read_back_amount_and_recipient(service):
  res = await service.interpret_utterance(
    utterance="send 5k to David", manifest=CUSTOM_NAMES_MANIFEST
  )
  assert res.params == {"beneficiary": "David", "amount": 500_000}
  assert "5,000 Naira" in res.spoken_prompt
  assert "David" in res.spoken_prompt


@pytest.mark.asyncio
async def test_legacy_string_amount_is_read_back(service):
  res = await service.interpret_utterance(
    utterance="send 5k to David", manifest=LEGACY_MANIFEST
  )
  assert set(res.params) == {"amount_raw", "amount_numeric_estimate", "recipient"}
  assert res.params["amount_raw"] == "5k"
  assert "5,000 Naira" in res.spoken_prompt
  assert "David" in res.spoken_prompt


@pytest.mark.asyncio
async def test_dialog_continuation_drops_undeclared_and_stale_params(service):
  res = await service.interpret_utterance(
    utterance="5000",
    manifest=CUSTOM_NAMES_MANIFEST,
    dialog_state={
      "pending_action_id": "send_money",
      "filled_params": {"beneficiary": "David", "amount_kobo": 100, "pin": "1234"},
    },
  )
  assert res.outcome == "action"
  assert res.params == {"beneficiary": "David", "amount": 500_000}
  # The spoken amount is the one the user just said, not the stale client value
  assert "5,000 Naira" in res.spoken_prompt
  assert "1 Naira" not in res.spoken_prompt


@pytest.mark.asyncio
async def test_dialog_continuation_without_recipient_asks_for_it(service):
  res = await service.interpret_utterance(
    utterance="5000",
    manifest=CUSTOM_NAMES_MANIFEST,
    dialog_state={"pending_action_id": "send_money", "filled_params": {}},
  )
  assert res.params == {"amount": 500_000}
  assert res.missing_params == ["beneficiary"]
  assert res.clarification_reason == "missing_recipient"


@pytest.mark.asyncio
async def test_dialog_continuation_with_no_declared_params_returns_none(service):
  res = await service.interpret_utterance(
    utterance="2000",
    manifest=[NO_PARAMS_ACTION],
    dialog_state={
      "pending_action_id": "freeze_card",
      "filled_params": {"amount_kobo": 500_000, "pin": "1234"},
    },
  )
  # No amount param declared: the shortcut is skipped and nothing undeclared leaks
  assert "amount_kobo" not in res.params
  assert "pin" not in res.params


@pytest.mark.asyncio
async def test_dialog_continuation_skips_shortcut_without_amount_param(service):
  res = await service.interpret_utterance(
    utterance="2000",
    manifest=[NO_AMOUNT_ACTION],
    dialog_state={
      "pending_action_id": "add_beneficiary",
      "filled_params": {"recipient": "David"},
    },
  )
  # Falls through to the model instead of returning a confidence-0.95 action
  assert res.confidence != 0.95
  assert "amount_minor" not in res.params


@pytest.mark.asyncio
async def test_bill_without_amount_asks_how_much_to_pay(service):
  res = await service.interpret_utterance(
    utterance="pay my electricity bill", manifest=[BILL_ACTION]
  )
  assert res.missing_params == ["amount_minor"]
  assert res.clarification_reason == "missing_amount"
  assert res.spoken_prompt == "How much would you like to pay?"


def test_param_roles_prefer_manifest_types_and_exact_names():
  roles = ParamRoles(
    {
      "id": "t",
      "params": [
        {"name": "recipient_bank", "type": "string"},
        {"name": "recipient", "type": "string"},
        {"name": "beneficiary", "type": "string"},
        {"name": "amount_currency", "type": "string"},
        {"name": "value", "type": "amount_minor"},
      ],
    }
  )
  assert roles.amount == ["value"]
  assert roles.recipient == "recipient"


def test_spoken_amount_and_recipient_handles_declared_names():
  assert spoken_amount_and_recipient(
    {"recipient": "David", "amount_minor": 500_000}
  ) == (
    500_000,
    "David",
  )
  assert spoken_amount_and_recipient({"beneficiary": "Ada", "amount": 200_000}) == (
    200_000,
    "Ada",
  )
  assert spoken_amount_and_recipient({"recipient": "Bola", "amount_raw": "5k"}) == (
    500_000,
    "Bola",
  )
  assert spoken_amount_and_recipient({}) == (0, "")


def test_confirmed_prompt_never_says_zero_naira_to_nobody():
  assert build_action_confirmed_prompt(0, "", lang="en") == "Confirmed."
  assert build_action_confirmed_prompt(200_000, "", lang="en") == "Confirmed."
  assert "David" in build_action_confirmed_prompt(500_000, "David", lang="en")

import pytest

from app.providers.mock_provider import MockLLMProvider
from app.services.interpreter import InterpreterService

MANIFEST = [
  {
    "id": "get_balance",
    "name": "get_balance",
    "description": "Check current account balance",
    "params": [],
  },
  {
    "id": "transfer_money",
    "name": "transfer_money",
    "description": "Transfer money to another account or person",
    "params": [
      {
        "name": "recipient",
        "type": "string",
        "required": True,
      },
      {
        "name": "amount_minor",
        "type": "amount_minor",
        "required": True,
      },
    ],
  },
]


@pytest.fixture
def service():
  provider = MockLLMProvider()
  return InterpreterService(provider=provider)


@pytest.mark.asyncio
async def test_pidgin_transfer_intent(service):
  # "abeg send 5k give David"
  res = await service.interpret_utterance(
    utterance="abeg send 5k give David", manifest=MANIFEST
  )
  assert res.type == "action"
  assert res.intent == "transfer_money"
  assert res.params["recipient"] == "David"
  assert res.params["amount_minor"] == 500_000
  assert "amount_kobo" not in res.params
  assert "5,000 Naira" in res.spoken_prompt
  assert "David" in res.spoken_prompt


@pytest.mark.asyncio
async def test_balance_intent(service):
  res = await service.interpret_utterance(
    utterance="wetin be my balance", manifest=MANIFEST
  )
  assert res.type == "action"
  assert res.intent == "get_balance"
  assert res.spoken_prompt == "Checking your account balance."


@pytest.mark.asyncio
async def test_zero_guessing_missing_recipient(service):
  # Missing recipient MUST trigger clarification question, NEVER guess!
  res = await service.interpret_utterance(utterance="send 5k", manifest=MANIFEST)
  assert res.type == "clarification"
  assert res.clarification_reason == "missing_recipient"
  assert "Who would you like to send money to?" in res.spoken_prompt


@pytest.mark.asyncio
async def test_zero_guessing_missing_amount(service):
  # Missing amount MUST trigger clarification question, NEVER guess!
  res = await service.interpret_utterance(
    utterance="transfer money to David", manifest=MANIFEST
  )
  assert res.type == "clarification"
  assert res.clarification_reason == "missing_amount"
  assert "How much would you like to send to David?" in res.spoken_prompt


@pytest.mark.asyncio
async def test_unsupported_action(service):
  # Out of scope banking request
  res = await service.interpret_utterance(
    utterance="book a flight to Abuja", manifest=MANIFEST
  )
  assert res.type == "clarification"
  assert res.clarification_reason in ["unknown_tool", "low_confidence"]


@pytest.mark.asyncio
async def test_interpret_params_contain_only_declared_keys(service):
  # 1. Standard manifest with recipient and amount_minor only
  res = await service.interpret_utterance(
    utterance="send 5000 naira to David",
    manifest=MANIFEST,
  )
  assert res.outcome == "action"
  assert set(res.params.keys()) == {"recipient", "amount_minor"}
  assert res.params["amount_minor"] == 500000
  assert res.params["recipient"] == "David"
  assert "amount_kobo" not in res.params
  assert "amount_raw" not in res.params

  # 2. Custom manifest with beneficiary instead of recipient
  custom_manifest = [
    {
      "id": "send_money",
      "name": "send money",
      "params": [
        {"name": "beneficiary", "type": "string", "required": True},
        {"name": "amount_minor", "type": "amount_minor", "required": True},
      ],
    }
  ]
  res2 = await service.interpret_utterance(
    utterance="send 5k to David",
    manifest=custom_manifest,
  )
  assert res2.outcome == "action"
  assert set(res2.params.keys()) == {"beneficiary", "amount_minor"}
  assert res2.params["beneficiary"] == "David"
  assert "recipient" not in res2.params
  assert "amount_kobo" not in res2.params

  # 3. Action without recipient (e.g. utility bill payment)
  bill_manifest = [
    {
      "id": "pay_bill",
      "name": "pay bill",
      "params": [
        {"name": "amount_minor", "type": "amount_minor", "required": True},
      ],
    }
  ]
  res3 = await service.interpret_utterance(
    utterance="pay 2000 naira for electricity",
    manifest=bill_manifest,
  )
  assert res3.outcome == "action"
  assert set(res3.params.keys()) == {"amount_minor"}
  assert res3.params["amount_minor"] == 200000
  assert "recipient" not in res3.params
  assert "amount_kobo" not in res3.params

  # 4. Action with no declared parameters returns empty params dict
  balance_res = await service.interpret_utterance(
    utterance="check my balance",
    manifest=MANIFEST,
  )
  assert balance_res.outcome == "action"
  assert balance_res.params == {}

import pytest

from app.providers.mock_provider import MockLLMProvider
from app.services.interpreter import InterpreterService

MANIFEST = [
    {
        "name": "get_balance",
        "description": "Check current account balance",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "transfer_money",
        "description": "Transfer money to another account or person",
        "parameters": {
            "type": "object",
            "properties": {
                "amount_raw": {"type": "string"},
                "amount_numeric_estimate": {"type": "number"},
                "recipient": {"type": "string"},
            },
            "required": ["amount_raw", "recipient"],
        },
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
    assert res.params["amount_kobo"] == 500_000
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

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.providers.gemini_provider import (
    GeminiProvider,
    sanitize_schema_for_gemini,
)
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
                "amount_raw": {
                    "type": "string",
                    "description": "Raw spoken amount phrase",
                },
                "amount_numeric_estimate": {
                    "type": "number",
                    "description": "Estimated numeric Naira value",
                },
                "recipient": {"type": "string", "description": "Name of recipient"},
            },
            "required": ["amount_raw", "recipient"],
        },
    },
]


def test_gemini_schema_sanitization():
    raw_schema = {
        "type": "object",
        "properties": {
            "amount_raw": {"type": "string"},
            "amount_numeric_estimate": {"type": "number"},
            "recipient": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["amount_raw", "recipient"],
    }
    cleaned = sanitize_schema_for_gemini(raw_schema)
    assert cleaned["type"] == "OBJECT"
    assert cleaned["properties"]["amount_raw"]["type"] == "STRING"
    assert cleaned["properties"]["amount_numeric_estimate"]["type"] == "NUMBER"
    assert cleaned["properties"]["recipient"]["type"] == "STRING"
    assert cleaned["properties"]["tags"]["type"] == "ARRAY"
    assert cleaned["properties"]["tags"]["items"]["type"] == "STRING"
    assert cleaned["required"] == ["amount_raw", "recipient"]


@pytest.mark.asyncio
async def test_gemini_missing_api_key_raises_value_error():
    provider = GeminiProvider(api_key="")
    with pytest.raises(ValueError, match="Gemini API key not configured"):
        await provider.interpret(utterance="send 5k to David", manifest=MANIFEST)


@pytest.mark.asyncio
async def test_gemini_tool_call_parsing():
    provider = GeminiProvider(api_key="fake-gemini-key")

    mock_gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "functionCall": {
                                "name": "transfer_money",
                                "args": {
                                    "recipient": "David",
                                    "amount_raw": "5k",
                                    "amount_numeric_estimate": 5000,
                                },
                            }
                        }
                    ],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ]
    }

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = mock_gemini_response
    mock_response.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", return_value=mock_response):
        result = await provider.interpret(
            utterance="abeg send 5k give David",
            manifest=MANIFEST,
        )

    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.name == "transfer_money"
    assert call.arguments["recipient"] == "David"
    assert call.arguments["amount_raw"] == "5k"
    assert call.arguments["amount_numeric_estimate"] == 5000
    assert result.confidence >= 0.95


@pytest.mark.asyncio
async def test_gemini_out_of_scope_text_response():
    provider = GeminiProvider(api_key="fake-gemini-key")

    mock_gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "I can only assist with account balance inquiries and money transfers."
                        }
                    ],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ]
    }

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = mock_gemini_response
    mock_response.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", return_value=mock_response):
        result = await provider.interpret(
            utterance="what is the weather in Lagos?",
            manifest=MANIFEST,
        )

    assert len(result.tool_calls) == 0
    assert result.confidence == 0.5
    assert "balance" in (result.text_content or "")


@pytest.mark.asyncio
async def test_gemini_end_to_end_interpreter_transfer():
    provider = GeminiProvider(api_key="fake-gemini-key")
    service = InterpreterService(provider=provider)

    mock_gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "functionCall": {
                                "name": "transfer_money",
                                "args": {
                                    "recipient": "David",
                                    "amount_raw": "5k",
                                    "amount_numeric_estimate": 5000,
                                },
                            }
                        }
                    ],
                    "role": "model",
                }
            }
        ]
    }

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = mock_gemini_response
    mock_response.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", return_value=mock_response):
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
async def test_gemini_end_to_end_zero_guessing_missing_recipient():
    provider = GeminiProvider(api_key="fake-gemini-key")
    service = InterpreterService(provider=provider)

    mock_gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "functionCall": {
                                "name": "transfer_money",
                                "args": {
                                    "recipient": None,
                                    "amount_raw": "5k",
                                    "amount_numeric_estimate": 5000,
                                },
                            }
                        }
                    ],
                    "role": "model",
                }
            }
        ]
    }

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = mock_gemini_response
    mock_response.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", return_value=mock_response):
        res = await service.interpret_utterance(utterance="send 5k", manifest=MANIFEST)

    assert res.type == "clarification"
    assert res.clarification_reason == "missing_recipient"
    assert "Who would you like to send money to?" in res.spoken_prompt

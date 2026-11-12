"""Tests for dialog continuation, list dialog_state, 24h session expiry, empty session_id, and Gemini 429 retry logic."""

import time

import aiosqlite
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.db.database import get_session, init_db
from app.main import app
from app.providers.base import (
  BaseLLMProvider,
  LLMInterpretationResult,
  ProviderRateLimitedError,
  ToolCall,
)
from app.providers.gemini_provider import GeminiProvider
from app.services.interpreter import InterpreterService

TRANSFER_ACTION = {
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


class _RecordingProvider(BaseLLMProvider):
  def __init__(self, tool_calls: list[ToolCall] | None = None):
    self.tool_calls = tool_calls or []
    self.last_dialog_state = None

  async def interpret(
    self, utterance, manifest, screen_descriptor=None, dialog_state=None
  ):
    self.last_dialog_state = dialog_state
    return LLMInterpretationResult(tool_calls=self.tool_calls, confidence=0.95)


@pytest.mark.asyncio
async def test_list_dialog_state_does_not_crash_and_sanitizes_sensitive_keys():
  provider = _RecordingProvider(
    tool_calls=[
      ToolCall(
        name="transfer_money",
        arguments={"recipient": "David", "amount_raw": "5k"},
      )
    ]
  )
  service = InterpreterService(provider=provider)
  res = await service.interpret_utterance(
    utterance="send 5k to David",
    manifest=[TRANSFER_ACTION],
    dialog_state=[{"step": 1, "balance": 999999, "pin": "1234", "value": "secret"}],
  )
  assert res.outcome == "action"
  assert res.params["amount_minor"] == 500_000
  assert provider.last_dialog_state == [{"step": 1}]


@pytest.mark.asyncio
async def test_dialog_continuation_checks_remaining_missing_params():
  provider = _RecordingProvider()
  service = InterpreterService(provider=provider)

  # 1. Recipient still missing when user replies with amount only
  res_partial = await service.interpret_utterance(
    utterance="5000",
    manifest=[TRANSFER_ACTION],
    dialog_state={"pending_action_id": "transfer_money", "filled_params": {}},
  )
  assert res_partial.outcome == "action"
  assert res_partial.action_id == "transfer_money"
  assert res_partial.params["amount_minor"] == 500_000
  assert res_partial.missing_params == ["recipient"]

  # 2. Recipient already filled -> action becomes complete
  res_complete = await service.interpret_utterance(
    utterance="1500 naira",
    manifest=[TRANSFER_ACTION],
    dialog_state={
      "pending_action_id": "transfer_money",
      "filled_params": {"recipient": "Amaka"},
    },
  )
  assert res_complete.outcome == "action"
  assert res_complete.action_id == "transfer_money"
  assert res_complete.params["recipient"] == "Amaka"
  assert res_complete.params["amount_minor"] == 150_000
  assert res_complete.missing_params == []
  assert "Amaka" in (res_complete.spoken_prompt or "")


@pytest.mark.asyncio
async def test_expired_session_rejected_after_24_hours():
  await init_db()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
    sess = await c.post("/v1/sessions", json={"locale": "en-NG"})
    assert sess.status_code == 200
    sid = sess.json()["session_id"]

    # Backdate created_at beyond 24 hours (86400s)
    expired_ts = int(time.time()) - 86401
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
      await db.execute(
        "UPDATE sessions SET created_at = ? WHERE session_id = ?",
        (expired_ts, sid),
      )
      await db.commit()

    assert await get_session(sid) is None

    res_interp = await c.post(
      "/v1/interpret",
      json={
        "session_id": sid,
        "text": "send 5k to David",
        "actions": [TRANSFER_ACTION],
      },
    )
    assert res_interp.status_code == 401
    assert res_interp.json()["error"]["code"] == "session_expired"

    res_conf = await c.post(
      "/v1/confirmations",
      json={
        "session_id": sid,
        "action_id": "transfer_money",
        "params": {"recipient": "David", "amount_minor": 500_000},
      },
    )
    assert res_conf.status_code == 401
    assert res_conf.json()["error"]["code"] == "session_expired"


@pytest.mark.asyncio
async def test_empty_session_id_on_confirm_returns_400_bad_request():
  await init_db()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
    res = await c.post(
      "/v1/confirmations/pc_any/confirm",
      json={"session_id": "", "method": "voice", "utterance": "confirm"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_gemini_429_with_cooldown_fails_fast_while_503_retries_once():
  calls_429 = 0

  def handle_429(request):
    nonlocal calls_429
    calls_429 += 1
    return httpx.Response(
      429,
      json={
        "error": {
          "code": 429,
          "message": "Quota exceeded",
          "details": [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"}
          ],
        }
      },
    )

  p_429 = GeminiProvider(
    api_key="test-key",
    client=httpx.AsyncClient(transport=httpx.MockTransport(handle_429)),
  )
  with pytest.raises(ProviderRateLimitedError) as exc:
    await p_429.interpret("send 5k to David", [TRANSFER_ACTION])
  assert exc.value.retry_after == 30
  assert calls_429 == 1

  calls_503 = 0

  def handle_503_then_200(request):
    nonlocal calls_503
    calls_503 += 1
    if calls_503 == 1:
      return httpx.Response(503, json={"error": {"message": "Temporarily unavailable"}})
    return httpx.Response(
      200,
      json={
        "candidates": [
          {
            "content": {
              "parts": [
                {
                  "functionCall": {
                    "name": "transfer_money",
                    "args": {"recipient": "David", "amount_raw": "5k"},
                  }
                }
              ]
            },
            "finishReason": "STOP",
          }
        ]
      },
    )

  p_503 = GeminiProvider(
    api_key="test-key",
    client=httpx.AsyncClient(transport=httpx.MockTransport(handle_503_then_200)),
  )
  result = await p_503.interpret("send 5k to David", [TRANSFER_ACTION])
  assert calls_503 == 2
  assert len(result.tool_calls) == 1

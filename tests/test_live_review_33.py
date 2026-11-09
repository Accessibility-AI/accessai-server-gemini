"""Fixes from the live deployment review (#33): rate limits, error leakage, CORS, demo page,
and the session_id / sensitivity loopholes."""

import json
from types import SimpleNamespace

import httpx
import pytest
from groq import RateLimitError as GroqRateLimitError
from httpx import ASGITransport, AsyncClient

from app.api.routes import get_interpreter_service
from app.config import settings
from app.db.database import init_db
from app.main import app
from app.providers.base import ProviderRateLimitedError, retry_after_seconds
from app.providers.gemini_provider import GeminiProvider
from app.providers.groq_provider import GroqProvider
from app.services.interpreter import InterpreterService

GOOGLE_429 = {
  "error": {
    "code": 429,
    "message": "Quota exceeded: SECRET-UPSTREAM-DETAIL",
    "status": "RESOURCE_EXHAUSTED",
    "details": [
      {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "55s"}
    ],
  }
}
TRANSFER = {
  "id": "transfer_money",
  "name": "send money",
  "description": "Send money",
  "params": [{"name": "recipient", "type": "string", "required": True}],
}


@pytest.fixture
async def client():
  await init_db()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
    yield c
  app.dependency_overrides.clear()


def _use_gemini_returning(status: int, body: dict):
  transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
  provider = GeminiProvider(
    api_key="test-key", client=httpx.AsyncClient(transport=transport)
  )
  app.dependency_overrides[get_interpreter_service] = lambda: InterpreterService(
    provider
  )


async def _interpret(client):
  sid = (await client.post("/v1/sessions", json={})).json()["session_id"]
  return await client.post(
    "/v1/interpret",
    json={"session_id": sid, "text": "send 5k to David", "actions": [TRANSFER]},
  )


# --- 2. Upstream errors ---------------------------------------------------------------


def test_retry_after_parsing():
  assert retry_after_seconds("55s") == 55
  assert retry_after_seconds("12") == 12
  assert retry_after_seconds("0.2s") == 1
  assert retry_after_seconds(None) is None
  assert retry_after_seconds("soon") is None


async def test_gemini_429_becomes_rate_limited_without_upstream_body(client):
  _use_gemini_returning(429, GOOGLE_429)
  res = await _interpret(client)
  assert res.status_code == 429
  assert res.json()["error"]["code"] == "rate_limited"
  assert res.headers["retry-after"] == "55"
  assert "SECRET-UPSTREAM-DETAIL" not in res.text
  assert "RESOURCE_EXHAUSTED" not in res.text


async def test_other_gemini_errors_do_not_leak_upstream_body(client):
  _use_gemini_returning(400, {"error": {"message": "SECRET-UPSTREAM-DETAIL"}})
  res = await _interpret(client)
  assert res.status_code == 500
  assert res.json() == {"error": {"code": "server", "message": "Interpretation failed"}}


async def test_groq_rate_limit_maps_to_provider_rate_limited():
  request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
  response = httpx.Response(429, headers={"retry-after": "7"}, request=request)

  async def create(**kwargs):
    raise GroqRateLimitError("rate limited", response=response, body=None)

  provider = GroqProvider(api_key="test-key")
  provider.client = SimpleNamespace(
    chat=SimpleNamespace(completions=SimpleNamespace(create=create))
  )
  with pytest.raises(ProviderRateLimitedError) as exc:
    await provider.interpret("send 5k to David", [TRANSFER])
  assert exc.value.retry_after == 7


# --- 3. CORS --------------------------------------------------------------------------


async def test_cors_does_not_allow_arbitrary_origins(client):
  res = await client.options(
    "/v1/sessions",
    headers={
      "Origin": "https://evil.example",
      "Access-Control-Request-Method": "POST",
    },
  )
  assert "access-control-allow-origin" not in res.headers
  assert res.headers.get("access-control-allow-credentials") != "true"

  res = await client.post(
    "/v1/sessions", json={}, headers={"Origin": "https://evil.example"}
  )
  assert "access-control-allow-origin" not in res.headers


# --- 4. Demo page ---------------------------------------------------------------------


async def test_demo_page_off_by_default(client, monkeypatch):
  monkeypatch.setattr(settings, "ENABLE_DEMO_PAGE", False)
  res = await client.get("/")
  assert res.headers["content-type"].startswith("application/json")
  assert res.json()["docs"] == "/docs"


async def test_demo_page_served_when_enabled(client, monkeypatch):
  monkeypatch.setattr(settings, "ENABLE_DEMO_PAGE", True)
  res = await client.get("/demo")
  assert res.headers["content-type"].startswith("text/html")


# --- 5.3 / 5.5 Schema loopholes -------------------------------------------------------


async def test_client_sensitivity_cannot_skip_device_auth(client):
  sid = (await client.post("/v1/sessions", json={})).json()["session_id"]
  pending = await client.post(
    "/v1/confirmations",
    json={
      "session_id": sid,
      "action_id": "get_statement",
      "params": {"month": "october"},
      "sensitivity": "read",
    },
  )
  assert pending.status_code == 200
  res = await client.post(
    f"/v1/confirmations/{pending.json()['pending_id']}/confirm",
    json={"session_id": sid, "method": "voice", "utterance": "confirm"},
  )
  assert res.status_code == 401
  assert res.json()["error"]["code"] == "auth_invalid"


async def test_session_id_required_on_confirm_and_cancel(client):
  res = await client.post(
    "/v1/confirmations/pc_x/confirm", json={"method": "voice", "utterance": "confirm"}
  )
  assert res.status_code == 400
  assert res.json()["error"]["code"] == "bad_request"

  res = await client.post("/v1/confirmations/pc_x/cancel")
  assert res.status_code == 400
  assert res.json()["error"]["code"] == "bad_request"


def test_schema_marks_session_id_required_and_drops_loopholes():
  schemas = app.openapi()["components"]["schemas"]
  assert "session_id" in schemas["ConfirmRequest"]["required"]
  assert "session_id" in schemas["CancelRequest"]["required"]
  assert "session_id" not in schemas["SessionCreateRequest"]["properties"]
  assert "sensitivity" not in schemas["PendingConfirmationRequest"]["properties"]
  interpret = app.openapi()["paths"]["/v1/interpret"]["post"]["responses"]
  assert "rate_limited" in json.dumps(interpret["429"])

"""End-to-end probe: the API exactly as the Android SDK 0.2.0 (contract v1.1) calls it.

Ported from accessai-server's tests/probe_script.py and updated for contract v1.1
(sessions with device keys, session_id on every confirm, device-auth signatures).
The token is checked the way a bank backend would: against the published JWKS only.
"""

import base64
import json

import pytest
from httpx import ASGITransport, AsyncClient
from jwcrypto import jwk, jws

from app.crypto.device_auth import generate_test_device_key, sign_device_auth
from app.main import app

SDK_HASH = "b729a280eabf6c63b6a6b889bfa1b26d4dad23286f2e3a36b98028e35879cd47"
SDK_HEADERS = {"X-AccessAI-App": "accesspay-demo", "X-Request-Id": "probe-1"}
SDK_MANIFEST = [
  {
    "id": "get_balance",
    "name": "check your balance",
    "description": "Read the user's available balance",
    "sensitivity": "read",
    "requires_confirmation": False,
    "params": [],
    "examples": ["what is my balance"],
  },
  {
    "id": "transfer_money",
    "name": "send money",
    "description": "Send money to a saved beneficiary",
    "sensitivity": "sensitive",
    "requires_confirmation": True,
    "params": [
      {
        "name": "recipient",
        "type": "string",
        "description": "Beneficiary name as the user said it",
        "required": True,
      },
      {
        "name": "amount_minor",
        "type": "amount_minor",
        "description": "Amount in kobo",
        "required": True,
      },
    ],
    "examples": ["send 5000 naira to David"],
  },
]


def _b64json(segment: str) -> dict:
  return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def _interpret_body(session_id: str, text: str) -> dict:
  return {
    "session_id": session_id,
    "text": text,
    "locale": "en-NG",
    "screen": {"screen_id": "home", "title": "Home", "summary": None, "elements": []},
    "actions": SDK_MANIFEST,
    "dialog": {
      "pending_action_id": None,
      "filled_params": {},
      "missing_params": [],
      "awaiting": "none",
      "last_system_message": None,
    },
  }


@pytest.fixture
async def client():
  async with AsyncClient(
    transport=ASGITransport(app=app), base_url="http://test", headers=SDK_HEADERS
  ) as c:
    yield c


@pytest.mark.parametrize(
  "text", ["Send 5000 to David Adeyemi", "Send 5,000 to David Adeyemi"]
)
async def test_sdk_interpret_request_returns_integer_kobo(client, text):
  sess = await client.post(
    "/v1/sessions", json={"app_id": "accesspay-demo", "locale": "en-NG"}
  )
  assert sess.status_code == 200
  sid = sess.json()["session_id"]

  res = await client.post("/v1/interpret", json=_interpret_body(sid, text))
  assert res.status_code == 200
  body = res.json()
  assert body["outcome"] == "action"
  assert body["action_id"] == "transfer_money"
  assert body["params"]["amount_minor"] == 500_000
  assert isinstance(body["params"]["amount_minor"], int)
  assert body["params"]["recipient"].startswith("David")
  assert body["missing_params"] == []


async def test_sdk_confirmation_flow_end_to_end(client):
  device_key, private_key = generate_test_device_key(kid="probe-device")
  sess = await client.post(
    "/v1/sessions",
    json={"app_id": "accesspay-demo", "locale": "en-NG", "device_key": device_key},
  )
  sid = sess.json()["session_id"]

  # Pending confirmation with resolved params hashes to the SDK's reference vector
  pending = await client.post(
    "/v1/confirmations",
    json={
      "session_id": sid,
      "action_id": "transfer_money",
      "params": {"amount_minor": 500000, "recipient": "ben_001"},
    },
  )
  assert pending.status_code == 200
  pending_id = pending.json()["pending_id"]
  assert pending.json()["params_sha256"] == SDK_HASH

  auth = sign_device_auth(
    private_key=private_key,
    session_id=sid,
    pending_id=pending_id,
    action_id="transfer_money",
    params_sha256=SDK_HASH,
    kid=device_key["kid"],
  )

  async def confirm(utterance: str):
    return await client.post(
      f"/v1/confirmations/{pending_id}/confirm",
      json={"session_id": sid, "method": "voice", "utterance": utterance, "auth": auth},
    )

  # Valid device auth alone is not enough: the phrase must be a strict confirmation
  for phrase in ["yes", "okay", "don't confirm", "confirm transfer to David", ""]:
    res = await confirm(phrase)
    assert res.status_code == 422, phrase
    assert res.json()["error"]["code"] == "not_a_confirmation"

  res = await confirm("confirm transfer")
  assert res.status_code == 200
  token = res.json()["token"]

  # Verify as the bank would: only the published JWKS, no server internals
  jwks = (await client.get("/.well-known/jwks.json")).json()
  verifier = jws.JWS()
  verifier.deserialize(token)
  verifier.verify(jwk.JWK(**jwks["keys"][0]))

  header, claims = (_b64json(part) for part in token.split(".")[:2])
  assert header["alg"] == "EdDSA"
  assert claims["iss"] == "accessai"
  assert claims["sub"] == sid
  assert claims["aid"] == "transfer_money"
  assert claims["ph"] == SDK_HASH
  assert claims["jti"] == res.json()["nonce"]
  assert claims["amr"] == ["biometric"]
  assert claims["exp"] - claims["iat"] <= 60

  # Single use
  again = await confirm("confirm transfer")
  assert again.status_code == 409
  assert again.json()["error"]["code"] == "confirmation_used"


async def test_errors_use_contract_envelope(client):
  sess = await client.post("/v1/sessions", json={"app_id": "accesspay-demo"})
  sid = sess.json()["session_id"]
  res = await client.post(
    "/v1/confirmations/does-not-exist/confirm",
    json={"session_id": sid, "utterance": "confirm"},
  )
  assert res.status_code == 404
  assert res.json() == {
    "error": {"code": "not_found", "message": res.json()["error"]["message"]}
  }

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.crypto.device_auth import generate_test_device_key, sign_device_auth
from app.crypto.jws_signer import hash_params, signer
from app.db.database import init_db
from app.main import app

ACTIONS = [
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
        "description": "Beneficiary name as said by user",
        "required": True,
      },
      {
        "name": "amount_minor",
        "type": "amount_minor",
        "description": "Amount in kobo",
        "required": True,
      },
    ],
  },
  {
    "id": "get_balance",
    "name": "check balance",
    "description": "Check current available balance",
    "sensitivity": "read",
    "requires_confirmation": False,
    "params": [],
  },
]


@pytest.mark.asyncio
async def test_concurrency_single_use_atomicity():
  """Security Bug 1: 20 concurrent /confirm calls on one pending confirmation

  must return exactly ONE 200 and 19 409 confirmation_used.
  """
  await init_db()
  device_key, private_key = generate_test_device_key(kid="concurrency-test-key")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    # Create session with device key
    sess_res = await client.post(
      "/v1/sessions",
      json={"device_key": device_key, "locale": "en-NG"},
    )
    assert sess_res.status_code == 200
    session_id = sess_res.json()["session_id"]

    # Create pending confirmation
    params = {"amount_minor": 500000, "recipient": "David"}
    p_res = await client.post(
      "/v1/confirmations",
      json={
        "session_id": session_id,
        "action_id": "transfer_money",
        "params": params,
      },
    )
    assert p_res.status_code == 200
    pending_id = p_res.json()["pending_id"]
    params_hash = p_res.json()["params_sha256"]

    auth = sign_device_auth(
      private_key=private_key,
      session_id=session_id,
      pending_id=pending_id,
      action_id="transfer_money",
      params_sha256=params_hash,
      kid=device_key["kid"],
    )

    confirm_body = {
      "session_id": session_id,
      "method": "voice",
      "utterance": "confirm transfer",
      "auth": auth,
    }

    # Fire 20 concurrent /confirm requests
    async def call_confirm():
      return await client.post(
        f"/v1/confirmations/{pending_id}/confirm",
        json=confirm_body,
      )

    responses = await asyncio.gather(*(call_confirm() for _ in range(20)))

    status_codes = [r.status_code for r in responses]
    num_200 = status_codes.count(200)
    num_409 = status_codes.count(409)

    assert num_200 == 1, (
      f"Expected exactly 1 200 OK, got {num_200} (statuses: {status_codes})"
    )
    assert num_409 == 19, (
      f"Expected 19 409 Conflict, got {num_409} (statuses: {status_codes})"
    )

    # Verify that all 409 responses carry confirmation_used error code
    for r in responses:
      if r.status_code == 409:
        body = r.json()
        assert body["error"]["code"] == "confirmation_used"


@pytest.mark.asyncio
async def test_no_device_auth_rejects_with_auth_invalid():
  """Security Bug 2: A sensitive action confirms with auth_invalid (401) if session has no device key."""
  await init_db()
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    # Create session WITHOUT device key
    sess_res = await client.post("/v1/sessions", json={"locale": "en-NG"})
    assert sess_res.status_code == 200
    session_id = sess_res.json()["session_id"]

    p_res = await client.post(
      "/v1/confirmations",
      json={
        "session_id": session_id,
        "action_id": "transfer_money",
        "params": {"amount_minor": 100000, "recipient": "Ngozi"},
      },
    )
    assert p_res.status_code == 200
    pending_id = p_res.json()["pending_id"]

    # Attempt to confirm sensitive action (touch) without auth
    c_res = await client.post(
      f"/v1/confirmations/{pending_id}/confirm",
      json={"session_id": session_id, "method": "touch"},
    )
    assert c_res.status_code == 401
    assert c_res.json()["error"]["code"] == "auth_invalid"


@pytest.mark.asyncio
async def test_pending_on_unknown_session_rejects_session_expired():
  """Security Bug 3: /confirmations rejects unknown session with 401 session_expired."""
  await init_db()
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    res = await client.post(
      "/v1/confirmations",
      json={
        "session_id": "non-existent-session-id",
        "action_id": "transfer_money",
        "params": {"amount_minor": 500000, "recipient": "Bob"},
      },
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "session_expired"


@pytest.mark.asyncio
async def test_confirm_session_mismatch_rejects_not_found():
  """Security Bug 4: Confirm request with mismatched session_id returns 404 not_found."""
  await init_db()
  device_key, private_key = generate_test_device_key(kid="mismatch-key-1")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    sess1 = await client.post("/v1/sessions", json={"device_key": device_key})
    sess2 = await client.post("/v1/sessions", json={"device_key": device_key})
    sid1 = sess1.json()["session_id"]
    sid2 = sess2.json()["session_id"]

    p_res = await client.post(
      "/v1/confirmations",
      json={
        "session_id": sid1,
        "action_id": "transfer_money",
        "params": {"amount_minor": 200000, "recipient": "Alice"},
      },
    )
    pending_id = p_res.json()["pending_id"]

    auth = sign_device_auth(
      private_key=private_key,
      session_id=sid1,
      pending_id=pending_id,
      action_id="transfer_money",
      params_sha256=p_res.json()["params_sha256"],
      kid=device_key["kid"],
    )

    # Call confirm using sid2 instead of sid1
    c_res = await client.post(
      f"/v1/confirmations/{pending_id}/confirm",
      json={
        "session_id": sid2,
        "method": "voice",
        "utterance": "confirm",
        "auth": auth,
      },
    )
    assert c_res.status_code == 404
    assert c_res.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_token_privacy_and_claims_v1_1():
  """Privacy & Token claims: Token contains iss=accessai, aid, ph, jti, amr.

  Raw recipient and amount params MUST NOT be present in token.
  """
  await init_db()
  device_key, private_key = generate_test_device_key(kid="token-claims-key")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    sess = await client.post("/v1/sessions", json={"device_key": device_key})
    sid = sess.json()["session_id"]

    params = {"amount_minor": 500000, "recipient": "Secret Beneficiary"}
    p_res = await client.post(
      "/v1/confirmations",
      json={"session_id": sid, "action_id": "transfer_money", "params": params},
    )
    pending_id = p_res.json()["pending_id"]
    params_hash = p_res.json()["params_sha256"]

    auth = sign_device_auth(
      private_key=private_key,
      session_id=sid,
      pending_id=pending_id,
      action_id="transfer_money",
      params_sha256=params_hash,
      kid=device_key["kid"],
    )

    c_res = await client.post(
      f"/v1/confirmations/{pending_id}/confirm",
      json={"session_id": sid, "utterance": "confirm", "auth": auth},
    )
    assert c_res.status_code == 200
    token_str = c_res.json()["token"]

    payload = signer.verify_token(token_str)
    assert payload["iss"] == "accessai"
    assert payload["sub"] == sid
    assert payload["aid"] == "transfer_money"
    assert payload["ph"] == params_hash
    assert payload["jti"] == c_res.json()["nonce"]
    assert payload["amr"] == ["biometric"]

    # Verify privacy: raw params dictionary is NOT in the payload
    assert "params" not in payload
    assert "Secret Beneficiary" not in str(payload)


@pytest.mark.asyncio
async def test_canonical_hash_unicode_vector():
  """Canonical hash: ensure_ascii=False ensures correct hash for Yoruba diacritics."""
  params = {"amount_minor": 500000, "recipient": "Adébáyọ̀"}
  h = hash_params(params)
  assert isinstance(h, str) and len(h) == 64

  # Vector check from API_CONTRACT.md line 121
  ref_params = {"amount_minor": 500000, "recipient": "ben_001"}
  ref_hash = hash_params(ref_params)
  assert ref_hash == "b729a280eabf6c63b6a6b889bfa1b26d4dad23286f2e3a36b98028e35879cd47"


@pytest.mark.asyncio
async def test_client_chosen_session_id_is_ignored():
  """#33 / 5.5: POST /v1/sessions never accepts a client-chosen session_id; the server
  always generates a fresh one, so a known id can't be claimed or re-registered."""
  await init_db()
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    res1 = await client.post("/v1/sessions", json={"session_id": "custom-sid-123"})
    assert res1.status_code == 200
    assert "expires_at" in res1.json()
    assert res1.json()["session_id"] != "custom-sid-123"

    # Sending the same id again creates another, distinct session (no 500, no reuse)
    res2 = await client.post("/v1/sessions", json={"session_id": "custom-sid-123"})
    assert res2.status_code == 200
    assert res2.json()["session_id"] not in (
      "custom-sid-123",
      res1.json()["session_id"],
    )


@pytest.mark.asyncio
async def test_interpret_contract_v1_1_shape():
  """POST /v1/interpret: verifies request (text, actions, dialog) and response (outcome, action_id, params.amount_minor, missing_params, control, message)."""
  await init_db()
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    s_res = await client.post("/v1/sessions", json={"app_id": "accesspay-demo"})
    session_id = s_res.json()["session_id"]

    req_body = {
      "session_id": session_id,
      "text": "Send five thousand naira to David",
      "locale": "en-NG",
      "actions": ACTIONS,
      "dialog": {
        "pending_action_id": None,
        "filled_params": {},
        "missing_params": [],
        "awaiting": "none",
        "last_system_message": "Welcome to AccessPay",
      },
    }

    res = await client.post("/v1/interpret", json=req_body)
    assert res.status_code == 200
    data = res.json()
    assert data["outcome"] == "action"
    assert data["action_id"] == "transfer_money"
    assert isinstance(data["params"]["amount_minor"], int)
    assert data["params"]["amount_minor"] == 500000
    assert data["params"]["recipient"] == "David"
    assert data["missing_params"] == []
    assert data["control"] is None
    assert data["message"] is None
    assert "request_id" in data


@pytest.mark.asyncio
async def test_cancel_endpoint_204_idempotent():
  """POST /v1/confirmations/{id}/cancel returns 204 No Content and is idempotent."""
  await init_db()
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    s_res = await client.post("/v1/sessions", json={})
    sid = s_res.json()["session_id"]

    p_res = await client.post(
      "/v1/confirmations",
      json={
        "session_id": sid,
        "action_id": "transfer_money",
        "params": {"amount_minor": 10000, "recipient": "Kemi"},
      },
    )
    pid = p_res.json()["pending_id"]

    # Cancel returns 204
    c1 = await client.post(f"/v1/confirmations/{pid}/cancel", json={"session_id": sid})
    assert c1.status_code == 204

    # Idempotent: second cancel also returns 204
    c2 = await client.post(f"/v1/confirmations/{pid}/cancel", json={"session_id": sid})
    assert c2.status_code == 204

    # Cancel with mismatched session_id returns 404
    c3 = await client.post(
      f"/v1/confirmations/{pid}/cancel", json={"session_id": "other-session"}
    )
    assert c3.status_code == 404


@pytest.mark.asyncio
async def test_voice_confirm_not_a_confirmation_422():
  """Voice confirmation with invalid utterance returns 422 not_a_confirmation."""
  await init_db()
  device_key, private_key = generate_test_device_key(kid="voice-test-key")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    s_res = await client.post("/v1/sessions", json={"device_key": device_key})
    sid = s_res.json()["session_id"]

    p_res = await client.post(
      "/v1/confirmations",
      json={
        "session_id": sid,
        "action_id": "transfer_money",
        "params": {"amount_minor": 10000, "recipient": "Kemi"},
      },
    )
    pid = p_res.json()["pending_id"]
    auth = sign_device_auth(
      private_key=private_key,
      session_id=sid,
      pending_id=pid,
      action_id="transfer_money",
      params_sha256=p_res.json()["params_sha256"],
      kid=device_key["kid"],
    )

    res = await client.post(
      f"/v1/confirmations/{pid}/confirm",
      json={
        "session_id": sid,
        "method": "voice",
        "utterance": "sure go ahead",
        "auth": auth,
      },
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "not_a_confirmation"


@pytest.mark.asyncio
async def test_interpret_missing_params_clarify():
  """Partial request returns outcome=action with missing_params and filled params (Contract v1.1)."""
  await init_db()
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    s_res = await client.post("/v1/sessions", json={"app_id": "test-app"})
    sid = s_res.json()["session_id"]

    # Missing recipient: "Send 5000 naira" -> outcome=action, params={amount_minor: 500000}, missing_params=[recipient]
    res1 = await client.post(
      "/v1/interpret",
      json={"session_id": sid, "text": "Send 5000 naira", "actions": ACTIONS},
    )
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["outcome"] == "action"
    assert d1["action_id"] == "transfer_money"
    assert "recipient" in d1["missing_params"]
    assert d1["params"]["amount_minor"] == 500000
    assert d1["message"] is not None

    # Missing amount: "Send money to David" -> outcome=action, params={recipient: David}, missing_params=[amount_minor]
    res2 = await client.post(
      "/v1/interpret",
      json={"session_id": sid, "text": "Send money to David", "actions": ACTIONS},
    )
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["outcome"] == "action"
    assert d2["action_id"] == "transfer_money"
    assert "amount_minor" in d2["missing_params"]
    assert d2["params"]["recipient"] == "David"
    assert d2["message"] is not None


@pytest.mark.asyncio
async def test_interpret_control_commands():
  """Control commands return outcome=control without touching LLM."""
  await init_db()
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    s_res = await client.post("/v1/sessions", json={"app_id": "test-app"})
    sid = s_res.json()["session_id"]

    for cmd in ["cancel", "stop", "repeat", "help", "describe screen"]:
      res = await client.post(
        "/v1/interpret",
        json={"session_id": sid, "text": cmd, "actions": ACTIONS},
      )
      assert res.status_code == 200
      data = res.json()
      assert data["outcome"] == "control"
      assert data["control"] is not None


@pytest.mark.asyncio
async def test_confirmation_replay_blocked_409():
  """Confirming an already confirmed transaction returns 409 confirmation_used."""
  await init_db()
  device_key, private_key = generate_test_device_key(kid="replay-key-1")
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    s_res = await client.post("/v1/sessions", json={"device_key": device_key})
    sid = s_res.json()["session_id"]

    p_res = await client.post(
      "/v1/confirmations",
      json={
        "session_id": sid,
        "action_id": "transfer_money",
        "params": {"amount_minor": 10000, "recipient": "Kemi"},
      },
    )
    pid = p_res.json()["pending_id"]
    auth = sign_device_auth(
      private_key=private_key,
      session_id=sid,
      pending_id=pid,
      action_id="transfer_money",
      params_sha256=p_res.json()["params_sha256"],
      kid=device_key["kid"],
    )

    # First confirm: 200 OK
    c1 = await client.post(
      f"/v1/confirmations/{pid}/confirm",
      json={
        "session_id": sid,
        "method": "voice",
        "utterance": "confirm",
        "auth": auth,
      },
    )
    assert c1.status_code == 200
    assert c1.json()["status"] == "confirmed"

    # Second confirm: 409 confirmation_used
    c2 = await client.post(
      f"/v1/confirmations/{pid}/confirm",
      json={
        "session_id": sid,
        "method": "voice",
        "utterance": "confirm",
        "auth": auth,
      },
    )
    assert c2.status_code == 409
    assert c2.json()["error"]["code"] == "confirmation_used"


@pytest.mark.asyncio
async def test_jwks_and_health_endpoints():
  """Verify JWKS format and health endpoint."""
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    h_res = await client.get("/health")
    assert h_res.status_code == 200
    assert h_res.json()["status"] == "healthy"

    j_res = await client.get("/.well-known/jwks.json")
    assert j_res.status_code == 200
    jwks = j_res.json()
    assert "keys" in jwks
    assert len(jwks["keys"]) >= 1
    key = jwks["keys"][0]
    assert key["kty"] == "OKP"
    assert key["crv"] == "Ed25519"
    assert key["use"] == "sig"


@pytest.mark.asyncio
async def test_dynamic_manifest_with_custom_param_names():
  """Service correctly recognizes amount_minor parameter when action is named send_cash with beneficiary."""
  await init_db()
  custom_actions = [
    {
      "id": "send_cash",
      "name": "send cash",
      "description": "Send cash to beneficiary",
      "sensitivity": "sensitive",
      "params": [
        {"name": "beneficiary", "type": "string", "required": True},
        {"name": "amount_minor", "type": "amount_minor", "required": True},
      ],
    }
  ]
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    s_res = await client.post("/v1/sessions", json={"app_id": "custom-app"})
    sid = s_res.json()["session_id"]

    res = await client.post(
      "/v1/interpret",
      json={
        "session_id": sid,
        "text": "Send 5000 naira to David",
        "actions": custom_actions,
      },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["outcome"] == "action"
    assert data["action_id"] == "send_cash"
    assert data["params"]["amount_minor"] == 500000


# ==============================================================================
# PROBE & RE-REVIEW TEST SUITE (H1, H2, Sensitivity, Partial Requests, pay_bill, Cancel)
# ==============================================================================


@pytest.mark.asyncio
async def test_h1_cannot_replace_device_key_on_existing_session():
  """H1 Fix: Re-POST /v1/sessions with an existing session_id CANNOT replace its device_key."""
  await init_db()
  key_a, priv_a = generate_test_device_key(kid="victim-key-a")
  key_b, priv_b = generate_test_device_key(kid="attacker-key-b")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    # Victim creates session with key_a
    res1 = await client.post("/v1/sessions", json={"device_key": key_a})
    assert res1.status_code == 200
    sid = res1.json()["session_id"]
    assert res1.json()["device_key"]["kid"] == "victim-key-a"

    # Attacker tries to hijack session by re-POSTing sid with key_b: the sid is
    # ignored, so the attacker only gets a separate session of their own
    res2 = await client.post(
      "/v1/sessions", json={"session_id": sid, "device_key": key_b}
    )
    assert res2.status_code == 200
    assert res2.json()["session_id"] != sid

    # Victim creates a sensitive transfer confirmation
    p_res = await client.post(
      "/v1/confirmations",
      json={
        "session_id": sid,
        "action_id": "transfer_money",
        "params": {"amount_minor": 500000, "recipient": "David"},
      },
    )
    assert p_res.status_code == 200
    pid = p_res.json()["pending_id"]
    p_sha = p_res.json()["params_sha256"]

    # Attacker tries to confirm using attacker key_b signature -> MUST FAIL (401 auth_invalid)
    auth_b = sign_device_auth(priv_b, sid, pid, "transfer_money", p_sha, key_b["kid"])
    c_b = await client.post(
      f"/v1/confirmations/{pid}/confirm",
      json={
        "session_id": sid,
        "method": "voice",
        "utterance": "confirm",
        "auth": auth_b,
      },
    )
    assert c_b.status_code == 401
    assert c_b.json()["error"]["code"] == "auth_invalid"

    # Victim confirms using victim key_a signature -> MUST SUCCEED (200 OK)
    auth_a = sign_device_auth(priv_a, sid, pid, "transfer_money", p_sha, key_a["kid"])
    c_a = await client.post(
      f"/v1/confirmations/{pid}/confirm",
      json={
        "session_id": sid,
        "method": "voice",
        "utterance": "confirm",
        "auth": auth_a,
      },
    )
    assert c_a.status_code == 200
    assert c_a.json()["status"] == "confirmed"


@pytest.mark.asyncio
async def test_h2_params_grounded_in_utterance_never_trust_hallucinations():
  """H2 Fix: Model params must be grounded in user utterance.

  - amount_minor 5000 from model for '5k' must be parsed to 500000 kobo.
  - Hallucinated amount on 'send money to David' must be marked missing.
  - Hallucinated recipient on 'send 5000 naira' must be marked missing.
  - Invented amount_raw not in utterance must be discarded.
  """
  from app.providers.base import BaseLLMProvider, LLMInterpretationResult, ToolCall
  from app.services.interpreter import InterpreterService

  class FakeHallucinatingProvider(BaseLLMProvider):
    def __init__(self, tool_args: dict):
      self.tool_args = tool_args

    async def interpret(
      self, utterance, manifest, screen_descriptor=None, dialog_state=None
    ):
      return LLMInterpretationResult(
        tool_calls=[ToolCall(name="transfer_money", arguments=self.tool_args)],
        confidence=0.96,
      )

  # 1. Model returns amount_minor: 5000 for "send 5k to David" -> MUST be 500000 kobo (₦5,000)
  p1 = FakeHallucinatingProvider({"recipient": "David", "amount_minor": 5000})
  s1 = InterpreterService(provider=p1)
  res1 = await s1.interpret_utterance("send 5k to David", ACTIONS)
  assert res1.outcome == "action"
  assert res1.params["amount_minor"] == 500000
  assert res1.params["amount_minor"] != 5000

  # 2. Model returns amount_minor: 50000000 for "send 5k to David" -> MUST be 500000 kobo
  p2 = FakeHallucinatingProvider({"recipient": "David", "amount_minor": 50000000})
  s2 = InterpreterService(provider=p2)
  res2 = await s2.interpret_utterance("send 5k to David", ACTIONS)
  assert res2.outcome == "action"
  assert res2.params["amount_minor"] == 500000

  # 3. Model invents an amount for "send money to David" -> amount_minor must be MISSING
  p3 = FakeHallucinatingProvider(
    {"recipient": "David", "amount_minor": 50000, "amount_raw": "500 naira"}
  )
  s3 = InterpreterService(provider=p3)
  res3 = await s3.interpret_utterance("send money to David", ACTIONS)
  assert res3.outcome == "action"
  assert "amount_minor" in res3.missing_params
  assert "amount_minor" not in res3.params
  assert res3.params.get("recipient") == "David"

  # 4. Model invents recipient "Alice" for "send 5000 naira" -> recipient must be MISSING
  p4 = FakeHallucinatingProvider({"recipient": "Alice", "amount_raw": "5000 naira"})
  s4 = InterpreterService(provider=p4)
  res4 = await s4.interpret_utterance("send 5000 naira", ACTIONS)
  assert res4.outcome == "action"
  assert "recipient" in res4.missing_params
  assert "recipient" not in res4.params
  assert res4.params.get("amount_minor") == 500000


@pytest.mark.asyncio
async def test_sensitivity_pay_bill_requires_auth():
  """Sensitivity Fix: pay_bill marked sensitive strictly requires device auth."""
  await init_db()
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    # Create session WITHOUT device key
    s_res = await client.post("/v1/sessions", json={"locale": "en-NG"})
    sid = s_res.json()["session_id"]

    # Create pending confirmation for pay_bill with sensitivity: sensitive
    p_res = await client.post(
      "/v1/confirmations",
      json={
        "session_id": sid,
        "action_id": "pay_bill",
        "sensitivity": "sensitive",
        "params": {"amount_minor": 200000, "biller": "electric"},
      },
    )
    assert p_res.status_code == 200
    pid = p_res.json()["pending_id"]

    # Confirm on key-less session MUST fail with 401 auth_invalid
    c_res = await client.post(
      f"/v1/confirmations/{pid}/confirm",
      json={"session_id": sid, "method": "touch"},
    )
    assert c_res.status_code == 401
    assert c_res.json()["error"]["code"] == "auth_invalid"


@pytest.mark.asyncio
async def test_partial_request_returns_action_with_missing_params():
  """Partial Request Fix: 'send money to Amaka' returns outcome=action with missing_params=[amount_minor]."""
  await init_db()
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    s_res = await client.post("/v1/sessions", json={"locale": "en-NG"})
    sid = s_res.json()["session_id"]

    res = await client.post(
      "/v1/interpret",
      json={"session_id": sid, "text": "send money to Amaka", "actions": ACTIONS},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["outcome"] == "action"
    assert data["action_id"] == "transfer_money"
    assert data["params"]["recipient"] == "Amaka"
    assert data["missing_params"] == ["amount_minor"]
    assert "Amaka" in data["message"]


@pytest.mark.asyncio
async def test_pay_bill_completes_without_recipient():
  """pay_bill Regression Fix: pay_bill only requires amount_minor and never asks for recipient."""
  await init_db()
  bill_actions = [
    {
      "id": "pay_bill",
      "name": "pay bill",
      "description": "Pay electricity or utility bill",
      "sensitivity": "sensitive",
      "params": [
        {"name": "amount_minor", "type": "amount_minor", "required": True},
      ],
    }
  ]
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    s_res = await client.post("/v1/sessions", json={"locale": "en-NG"})
    sid = s_res.json()["session_id"]

    res = await client.post(
      "/v1/interpret",
      json={
        "session_id": sid,
        "text": "pay 2000 naira for light",
        "actions": bill_actions,
      },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["outcome"] == "action"
    assert data["action_id"] == "pay_bill"
    assert data["params"]["amount_minor"] == 200000
    assert data["missing_params"] == []
    assert data["message"] is None


@pytest.mark.asyncio
async def test_cancel_requires_session_id_and_rejects_confirmed_with_409():
  """Cancel Fix: /cancel requires session_id and cannot cancel an already confirmed record (returns 409)."""
  await init_db()
  dk, priv = generate_test_device_key("cancel-test-key")
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    s_res = await client.post("/v1/sessions", json={"device_key": dk})
    sid = s_res.json()["session_id"]

    p_res = await client.post(
      "/v1/confirmations",
      json={
        "session_id": sid,
        "action_id": "transfer_money",
        "params": {"amount_minor": 10000, "recipient": "Kemi"},
      },
    )
    pid = p_res.json()["pending_id"]
    auth = sign_device_auth(
      priv, sid, pid, "transfer_money", p_res.json()["params_sha256"], dk["kid"]
    )

    # 1. Cancel without session_id -> MUST FAIL with 400 bad_request
    c_no_sid = await client.post(f"/v1/confirmations/{pid}/cancel", json={})
    assert c_no_sid.status_code == 400
    assert c_no_sid.json()["error"]["code"] == "bad_request"

    # 2. Confirm the pending transaction -> 200 OK
    c_ok = await client.post(
      f"/v1/confirmations/{pid}/confirm",
      json={
        "session_id": sid,
        "method": "voice",
        "utterance": "confirm",
        "auth": auth,
      },
    )
    assert c_ok.status_code == 200
    assert c_ok.json()["status"] == "confirmed"

    # 3. Cancel on an already confirmed transaction -> MUST FAIL with 409 confirmation_used
    c_after_conf = await client.post(
      f"/v1/confirmations/{pid}/cancel",
      json={"session_id": sid},
    )
    assert c_after_conf.status_code == 409
    assert c_after_conf.json()["error"]["code"] == "confirmation_used"

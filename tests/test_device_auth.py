import pytest
from httpx import ASGITransport, AsyncClient

from app.crypto.device_auth import generate_test_device_key, sign_device_auth
from app.crypto.jws_signer import hash_params, signer
from app.db.database import init_db
from app.main import app

MANIFEST = [
  {
    "name": "transfer_money",
    "description": "Transfer funds to another user",
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


@pytest.mark.asyncio
async def test_session_create_with_device_key():
  await init_db()
  device_key, _ = generate_test_device_key(kid="device-key-abc-123")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    res = await client.post(
      "/v1/sessions",
      json={
        "app_id": "accesspay-demo",
        "locale": "en-NG",
        "device_key": device_key,
        "metadata": {"test": "device_auth"},
      },
    )
    assert res.status_code in (200, 201)
    data = res.json()
    assert data["app_id"] == "accesspay-demo"
    assert data["locale"] == "en-NG"
    assert data["device_key"]["kid"] == "device-key-abc-123"
    assert data["device_key"]["alg"] == "ES256"


@pytest.mark.asyncio
async def test_device_auth_biometric_success_and_amr_claim():
  await init_db()
  device_key, private_key = generate_test_device_key(kid="bio-device-key-1")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    # 1. Create session with device_key
    res = await client.post(
      "/v1/sessions",
      json={
        "app_id": "accesspay-demo",
        "locale": "en-NG",
        "device_key": device_key,
      },
    )
    assert res.status_code in (200, 201)
    session_id = res.json()["session_id"]

    # 2. Create pending confirmation
    params = {"amount_kobo": 500_000, "recipient": "Amina", "amount_raw": "5k"}
    params_hash = hash_params(params)
    res = await client.post(
      "/v1/confirmations/pending",
      json={
        "session_id": session_id,
        "action": "transfer_money",
        "params": params,
      },
    )
    assert res.status_code in (200, 201)
    pending_id = res.json()["confirmation_id"]

    # 3. Create valid ES256 biometric auth signature
    auth = sign_device_auth(
      private_key=private_key,
      session_id=session_id,
      pending_id=pending_id,
      action_id="transfer_money",
      params_sha256=params_hash,
      kid=device_key["kid"],
      method="biometric",
    )

    # 4. Confirm with auth signature
    res = await client.post(
      f"/v1/confirmations/{pending_id}/confirm",
      json={
        "session_id": session_id,
        "method": "voice",
        "utterance": "confirm",
        "auth": auth,
      },
    )
    assert res.status_code == 200
    confirm_data = res.json()
    assert confirm_data["status"] == "confirmed"
    token = confirm_data["token"]

    # 5. Verify minted JWS token includes AMR claim: ["biometric"]
    token_payload = signer.verify_token(token)
    assert token_payload["amr"] == ["biometric"]
    assert token_payload["sub"] == session_id
    assert token_payload["action"] == "transfer_money"
    assert token_payload["params_hash"] == params_hash


@pytest.mark.asyncio
async def test_device_auth_device_credential_success():
  await init_db()
  device_key, private_key = generate_test_device_key(kid="pin-device-key-2")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    res = await client.post(
      "/v1/sessions",
      json={
        "app_id": "accesspay-demo",
        "locale": "en-NG",
        "device_key": device_key,
      },
    )
    session_id = res.json()["session_id"]

    params = {"amount_kobo": 1_000_000, "recipient": "Emeka", "amount_raw": "10k"}
    params_hash = hash_params(params)
    res = await client.post(
      "/v1/confirmations/pending",
      json={
        "session_id": session_id,
        "action": "transfer_money",
        "params": params,
      },
    )
    pending_id = res.json()["confirmation_id"]

    auth = sign_device_auth(
      private_key=private_key,
      session_id=session_id,
      pending_id=pending_id,
      action_id="transfer_money",
      params_sha256=params_hash,
      kid=device_key["kid"],
      method="device_credential",
    )

    res = await client.post(
      f"/v1/confirmations/{pending_id}/confirm",
      json={
        "session_id": session_id,
        "method": "voice",
        "utterance": "confirm",
        "auth": auth,
      },
    )
    assert res.status_code == 200
    token_payload = signer.verify_token(res.json()["token"])
    assert token_payload["amr"] == ["device_credential"]


@pytest.mark.asyncio
async def test_reject_proof_signed_by_wrong_key():
  """Mirrors SDK SecurityTest.kt: Proof signed by wrong key must be rejected with 401 auth_invalid."""
  await init_db()
  device_key, _correct_private_key = generate_test_device_key(kid="legit-key-1")
  _, wrong_private_key = generate_test_device_key(kid="attacker-key")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    # Register session with legitimate device_key
    res = await client.post(
      "/v1/sessions",
      json={"app_id": "accesspay-demo", "device_key": device_key},
    )
    session_id = res.json()["session_id"]

    params = {"amount_kobo": 500_000, "recipient": "David"}
    res = await client.post(
      "/v1/confirmations/pending",
      json={
        "session_id": session_id,
        "action": "transfer_money",
        "params": params,
      },
    )
    pending_id = res.json()["confirmation_id"]

    # Sign with WRONG private key, but claiming legit kid
    fake_auth = sign_device_auth(
      private_key=wrong_private_key,
      session_id=session_id,
      pending_id=pending_id,
      action_id="transfer_money",
      params_sha256=hash_params(params),
      kid=device_key["kid"],
    )

    res = await client.post(
      f"/v1/confirmations/{pending_id}/confirm",
      json={
        "session_id": session_id,
        "method": "voice",
        "utterance": "confirm",
        "auth": fake_auth,
      },
    )
    # Must return 401 auth_invalid and mint nothing
    assert res.status_code == 401
    body = res.json()
    assert body["error"]["code"] == "auth_invalid"
    assert "token" not in body


@pytest.mark.asyncio
async def test_reject_mismatched_kid():
  await init_db()
  device_key, private_key = generate_test_device_key(kid="registered-kid")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    res = await client.post(
      "/v1/sessions",
      json={"device_key": device_key},
    )
    session_id = res.json()["session_id"]

    params = {"amount_kobo": 200_000, "recipient": "Tunde"}
    res = await client.post(
      "/v1/confirmations/pending",
      json={
        "session_id": session_id,
        "action": "transfer_money",
        "params": params,
      },
    )
    pending_id = res.json()["confirmation_id"]

    auth = sign_device_auth(
      private_key=private_key,
      session_id=session_id,
      pending_id=pending_id,
      action_id="transfer_money",
      params_sha256=hash_params(params),
      kid="mismatched-kid-value",
    )

    res = await client.post(
      f"/v1/confirmations/{pending_id}/confirm",
      json={"session_id": session_id, "utterance": "confirm", "auth": auth},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "auth_invalid"


@pytest.mark.asyncio
async def test_reject_tampered_payload():
  await init_db()
  device_key, private_key = generate_test_device_key(kid="tamper-test-kid")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    res = await client.post(
      "/v1/sessions",
      json={"device_key": device_key},
    )
    session_id = res.json()["session_id"]

    params = {"amount_kobo": 500_000, "recipient": "David"}
    res = await client.post(
      "/v1/confirmations/pending",
      json={
        "session_id": session_id,
        "action": "transfer_money",
        "params": params,
      },
    )
    pending_id = res.json()["confirmation_id"]

    # Signature was made for action 'transfer_money', but someone signs 'drain_account'
    tampered_auth = sign_device_auth(
      private_key=private_key,
      session_id=session_id,
      pending_id=pending_id,
      action_id="drain_account",
      params_sha256=hash_params(params),
      kid=device_key["kid"],
    )

    res = await client.post(
      f"/v1/confirmations/{pending_id}/confirm",
      json={
        "session_id": session_id,
        "utterance": "confirm",
        "auth": tampered_auth,
      },
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "auth_invalid"


@pytest.mark.asyncio
async def test_reject_missing_auth_when_device_key_registered():
  await init_db()
  device_key, _ = generate_test_device_key(kid="key-no-auth")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    res = await client.post(
      "/v1/sessions",
      json={"device_key": device_key},
    )
    session_id = res.json()["session_id"]

    res = await client.post(
      "/v1/confirmations/pending",
      json={
        "session_id": session_id,
        "action": "transfer_money",
        "params": {"amount_kobo": 100_000, "recipient": "Ngozi"},
      },
    )
    pending_id = res.json()["confirmation_id"]

    # Confirm called WITHOUT auth payload
    res = await client.post(
      f"/v1/confirmations/{pending_id}/confirm",
      json={"session_id": session_id, "utterance": "confirm"},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "auth_invalid"


@pytest.mark.asyncio
async def test_direct_confirm_endpoint():
  """Tests POST /v1/confirm alias with pending_id in body."""
  await init_db()
  device_key, private_key = generate_test_device_key(kid="direct-confirm-kid")

  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://test") as client:
    res = await client.post(
      "/v1/sessions",
      json={"device_key": device_key},
    )
    session_id = res.json()["session_id"]

    params = {"amount_kobo": 300_000, "recipient": "Bisi", "amount_raw": "3k"}
    res = await client.post(
      "/v1/confirmations/pending",
      json={
        "session_id": session_id,
        "action": "transfer_money",
        "params": params,
      },
    )
    pending_id = res.json()["confirmation_id"]

    auth = sign_device_auth(
      private_key=private_key,
      session_id=session_id,
      pending_id=pending_id,
      action_id="transfer_money",
      params_sha256=hash_params(params),
      kid=device_key["kid"],
    )

    # Call POST /v1/confirm directly with pending_id
    res = await client.post(
      "/v1/confirm",
      json={
        "session_id": session_id,
        "pending_id": pending_id,
        "method": "voice",
        "utterance": "confirm",
        "auth": auth,
      },
    )
    assert res.status_code == 200
    assert res.json()["status"] == "confirmed"

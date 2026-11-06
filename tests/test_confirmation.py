import pytest

from app.crypto.device_auth import generate_test_device_key, sign_device_auth
from app.crypto.jws_signer import hash_params, signer
from app.db.database import create_session, init_db
from app.services.confirmation import ConfirmationService, is_strict_confirmation


@pytest.mark.asyncio
async def test_strict_confirmation_phrases():
  # Only strict confirmation phrases pass
  assert is_strict_confirmation("confirm") is True
  assert is_strict_confirmation("Confirm") is True
  assert is_strict_confirmation("  confirm!  ") is True
  assert is_strict_confirmation("I confirm") is True
  assert is_strict_confirmation("confirm transfer") is True
  assert is_strict_confirmation("confirm payment") is True
  assert is_strict_confirmation("confirm transaction") is True

  # Critical requirement: 'Yes', 'okay', or silence DOES NOT COUNT
  assert is_strict_confirmation("yes") is False
  assert is_strict_confirmation("Yes, please") is False
  assert is_strict_confirmation("yeah") is False
  assert is_strict_confirmation("okay") is False
  assert is_strict_confirmation("ok") is False
  assert is_strict_confirmation("sure") is False
  assert is_strict_confirmation("proceed") is False
  assert is_strict_confirmation("go ahead") is False
  assert is_strict_confirmation("") is False
  assert is_strict_confirmation("   ") is False


@pytest.mark.asyncio
async def test_jws_minting_and_verification():
  await init_db()
  session_id = "sess-test-123"
  action = "transfer_money"
  params = {"recipient": "David", "amount_minor": 500_000}
  nonce = "nonce-abc-456"

  token_str, _payload = signer.sign_token(
    session_id=session_id,
    action=action,
    params=params,
    nonce=nonce,
    ttl_seconds=60,
  )

  assert isinstance(token_str, str)
  assert len(token_str.split(".")) == 3  # Compact JWS format

  # Verify token payload
  verified_payload = signer.verify_token(token_str)
  assert verified_payload["iss"] == "accessai"
  assert verified_payload["sub"] == session_id
  assert verified_payload["aid"] == action
  assert verified_payload["jti"] == nonce
  assert verified_payload["ph"] == hash_params(params)
  # Privacy guarantee: Raw parameters must NOT be present in token
  assert "params" not in verified_payload


@pytest.mark.asyncio
async def test_confirmation_lifecycle():
  await init_db()
  session_id = "sess-lifecycle-test"
  device_key, private_key = generate_test_device_key(kid="lifecycle-key-1")
  await create_session(session_id=session_id, device_key=device_key)

  action = "transfer_money"
  params = {"recipient": "Amaka", "amount_minor": 2_000_000}
  p_hash = hash_params(params)

  # 1. Create pending confirmation
  pending = await ConfirmationService.create_pending(
    session_id=session_id,
    action=action,
    params=params,
  )
  conf_id = pending["confirmation_id"]
  assert pending["status"] == "pending"

  auth = sign_device_auth(
    private_key=private_key,
    session_id=session_id,
    pending_id=conf_id,
    action_id=action,
    params_sha256=p_hash,
    kid=device_key["kid"],
  )

  # 2. Reject informal words like 'yes' with 422 not_a_confirmation
  success, res, status_code = await ConfirmationService.process_confirm(
    confirmation_id=conf_id,
    session_id=session_id,
    utterance="yes",
    auth=auth,
  )
  assert success is False
  assert status_code == 422
  assert res["error"]["code"] == "not_a_confirmation"

  # 3. Confirm with strict phrase
  success, res, status_code = await ConfirmationService.process_confirm(
    confirmation_id=conf_id,
    session_id=session_id,
    utterance="confirm",
    auth=auth,
  )
  assert success is True
  assert status_code == 200
  assert res["status"] == "confirmed"
  assert "token" in res

  # 4. Token cannot be confirmed again (Single use -> 409 confirmation_used)
  success2, res2, status_code2 = await ConfirmationService.process_confirm(
    confirmation_id=conf_id,
    session_id=session_id,
    utterance="confirm",
    auth=auth,
  )
  assert success2 is False
  assert status_code2 == 409
  assert res2["error"]["code"] == "confirmation_used"


@pytest.mark.asyncio
async def test_confirmation_cancel():
  await init_db()
  session_id = "sess-cancel-test"
  await create_session(session_id=session_id)

  action = "transfer_money"
  params = {"recipient": "Chinedu", "amount_minor": 100_000}

  pending = await ConfirmationService.create_pending(
    session_id=session_id,
    action=action,
    params=params,
  )
  conf_id = pending["confirmation_id"]

  success, res, status_code = await ConfirmationService.process_cancel(
    confirmation_id=conf_id,
    session_id=session_id,
  )
  assert success is True
  assert status_code == 204
  assert res["status"] == "cancelled"

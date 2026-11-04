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

        assert num_200 == 1, f"Expected exactly 1 200 OK, got {num_200} (statuses: {status_codes})"
        assert num_409 == 19, f"Expected 19 409 Conflict, got {num_409} (statuses: {status_codes})"

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
async def test_session_duplicate_upsert_no_500():
    """POST /v1/sessions: duplicate session_id returns 200 and does not raise 500 IntegrityError."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res1 = await client.post("/v1/sessions", json={"session_id": "custom-sid-123"})
        assert res1.status_code == 200
        assert "expires_at" in res1.json()

        # Send same session_id again
        res2 = await client.post("/v1/sessions", json={"session_id": "custom-sid-123"})
        assert res2.status_code == 200
        assert res2.json()["session_id"] == "custom-sid-123"


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
            json={"session_id": sid, "action_id": "transfer_money", "params": {"amount_minor": 10000, "recipient": "Kemi"}},
        )
        pid = p_res.json()["pending_id"]

        # Cancel returns 204
        c1 = await client.post(f"/v1/confirmations/{pid}/cancel", json={"session_id": sid})
        assert c1.status_code == 204

        # Idempotent: second cancel also returns 204
        c2 = await client.post(f"/v1/confirmations/{pid}/cancel", json={"session_id": sid})
        assert c2.status_code == 204

        # Cancel with mismatched session_id returns 404
        c3 = await client.post(f"/v1/confirmations/{pid}/cancel", json={"session_id": "other-session"})
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
            json={"session_id": sid, "action_id": "transfer_money", "params": {"amount_minor": 10000, "recipient": "Kemi"}},
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
            json={"session_id": sid, "method": "voice", "utterance": "sure go ahead", "auth": auth},
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "not_a_confirmation"

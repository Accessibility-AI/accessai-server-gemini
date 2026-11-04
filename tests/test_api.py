import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.crypto.device_auth import generate_test_device_key, sign_device_auth
from app.crypto.jws_signer import hash_params
from app.db.database import init_db
from app.main import app


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    if not settings.GEMINI_API_KEY:
        monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")


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


@pytest.mark.asyncio
async def test_full_api_workflow():
    await init_db()
    device_key, private_key = generate_test_device_key(kid="api-test-key-1")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Health check
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "healthy"

        # 2. GET /.well-known/jwks.json
        res = await client.get("/.well-known/jwks.json")
        assert res.status_code == 200
        jwks = res.json()
        assert "keys" in jwks
        assert len(jwks["keys"]) > 0
        assert jwks["keys"][0]["kty"] == "OKP"
        assert jwks["keys"][0]["crv"] == "Ed25519"
        assert jwks["keys"][0]["alg"] == "EdDSA"

        # 3. POST /v1/sessions
        res = await client.post(
            "/v1/sessions",
            json={
                "device_key": device_key,
                "metadata": {"user": "test_blind_user"},
            },
        )
        assert res.status_code in (200, 201)
        session_data = res.json()
        session_id = session_data["session_id"]
        assert session_id is not None
        assert "expires_at" in session_data

        # 4. POST /v1/interpret (Valid transfer request in Nigerian English)
        res = await client.post(
            "/v1/interpret",
            json={
                "session_id": session_id,
                "text": "abeg send 5k give David",
                "actions": MANIFEST,
                "screen_descriptor": "Home Screen",
            },
        )
        assert res.status_code == 200
        interp = res.json()
        assert interp["outcome"] == "action"
        assert interp["action_id"] == "transfer_money"
        assert interp["params"]["amount_minor"] == 500_000
        assert interp["params"]["recipient"] == "David"

        # 5. POST /v1/confirmations
        res = await client.post(
            "/v1/confirmations",
            json={
                "session_id": session_id,
                "action_id": interp["action_id"],
                "params": interp["params"],
            },
        )
        assert res.status_code in (200, 201)
        pending = res.json()
        conf_id = pending["pending_id"]
        assert pending["status"] == "pending"
        assert "params_sha256" in pending
        assert pending["params_sha256"] == hash_params(interp["params"])

        # Sign valid device authentication
        auth = sign_device_auth(
            private_key=private_key,
            session_id=session_id,
            pending_id=conf_id,
            action_id="transfer_money",
            params_sha256=pending["params_sha256"],
            kid=device_key["kid"],
        )

        # 6. POST /v1/confirmations/{id}/confirm with informal "yes" -> MUST BE REJECTED (422)
        res = await client.post(
            f"/v1/confirmations/{conf_id}/confirm",
            json={
                "session_id": session_id,
                "utterance": "yes",
                "auth": auth,
            },
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "not_a_confirmation"

        # 7. POST /v1/confirmations/{id}/confirm with strict "confirm" -> SUCCESS (200)
        res = await client.post(
            f"/v1/confirmations/{conf_id}/confirm",
            json={
                "session_id": session_id,
                "utterance": "confirm",
                "auth": auth,
            },
        )
        assert res.status_code == 200
        confirmed = res.json()
        assert confirmed["status"] == "confirmed"
        assert "token" in confirmed
        assert len(confirmed["token"].split(".")) == 3
        assert "nonce" in confirmed

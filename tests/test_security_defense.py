import pytest
from httpx import ASGITransport, AsyncClient

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
    },
    {
        "name": "get_balance",
        "description": "Check current balance",
        "parameters": {"type": "object", "properties": {}},
    },
]


@pytest.mark.asyncio
async def test_reject_10_plus_digit_run_in_utterance():
    """Defence in depth: Utterances with 10+ digits (PIN, password, OTP, account number) must be rejected with 400 bad_request."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Nigerian phone number (11 digits)
        res = await client.post(
            "/v1/interpret",
            json={
                "session_id": "sess-sec-1",
                "utterance": "send money to 08012345678",
                "manifest": MANIFEST,
            },
        )
        assert res.status_code == 400
        body = res.json()
        assert body["error"]["code"] == "bad_request"
        assert "10+ digit run" in body["error"]["message"]

        # 10-digit account number
        res = await client.post(
            "/v1/interpret",
            json={
                "session_id": "sess-sec-1",
                "utterance": "wire money to account 0123456789",
                "manifest": MANIFEST,
            },
        )
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_reject_10_plus_digit_run_in_screen_descriptor():
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/v1/interpret",
            json={
                "session_id": "sess-sec-2",
                "utterance": "check balance",
                "manifest": MANIFEST,
                "screen_descriptor": "Account Details: 123456789012",
            },
        )
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_reject_screen_element_with_value():
    """Defence in depth: Screen elements must never contain element values."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Screen object with an element that has a value
        res = await client.post(
            "/v1/interpret",
            json={
                "session_id": "sess-sec-3",
                "utterance": "send 5k give David",
                "manifest": MANIFEST,
                "screen": {
                    "screen_id": "transfer_form",
                    "elements": [
                        {"id": "amount_input", "value": "5000"},
                        {"id": "recipient_input", "value": "David"},
                    ],
                },
            },
        )
        assert res.status_code == 400
        body = res.json()
        assert body["error"]["code"] == "bad_request"
        assert "Screen element contains a value" in body["error"]["message"]

        # Screen descriptor JSON string containing an element with a value
        res = await client.post(
            "/v1/interpret",
            json={
                "session_id": "sess-sec-3",
                "utterance": "send 5k give David",
                "manifest": MANIFEST,
                "screen_descriptor": '{"screen_id": "dash", "balance_card": {"value": "NGN 50,000"}}',
            },
        )
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_accept_sanitized_screen_and_number_removed():
    """Ensures properly sanitized SDK payloads (v1.1) pass cleanly."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/v1/sessions", json={"session_id": "sess-sec-4"})

        # 1. Screen formatted as login screen: {screen_id, title, sensitive: true}
        res = await client.post(
            "/v1/interpret",
            json={
                "session_id": "sess-sec-4",
                "utterance": "check my balance",
                "manifest": MANIFEST,
                "screen": {
                    "screen_id": "login_screen",
                    "title": "Welcome to AccessPay",
                    "sensitive": True,
                },
            },
        )
        assert res.status_code == 200
        assert res.json()["type"] == "action"

        # 2. Utterance with filtered [number removed]
        res = await client.post(
            "/v1/interpret",
            json={
                "session_id": "sess-sec-4",
                "utterance": "send 5k to [number removed] for Chidi",
                "manifest": MANIFEST,
                "screen_descriptor": "Transfer Screen",
            },
        )
        assert res.status_code == 200
        assert res.json()["type"] == "action"


@pytest.mark.asyncio
async def test_dialog_state_sanitization():
    """Ensures last_system_message, balance, and credentials in dialog_state are safely sanitized."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/v1/sessions", json={"session_id": "sess-sec-5"})

        res = await client.post(
            "/v1/interpret",
            json={
                "session_id": "sess-sec-5",
                "utterance": "send 10k give Fatima",
                "manifest": MANIFEST,
                "dialog_state": {
                    "last_system_message": "Your current balance is 150000",
                    "balance": 150000,
                    "previous_intent": "get_balance",
                },
            },
        )
        assert res.status_code == 200
        assert res.json()["params"]["recipient"] == "Fatima"

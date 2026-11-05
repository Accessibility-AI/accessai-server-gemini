import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.db.database import init_db
from app.main import app
from app.utils.logger import mask_pii, mask_utterance

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
async def test_no_utterance_retention_in_database():
    """Asserts that raw user utterances and transcripts are NEVER persisted in SQLite."""
    await init_db()
    unique_secret_utterance = "send 5k give Chinedu"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create session and interpret utterance
        res_sess = await client.post(
            "/v1/sessions", json={"metadata": {"test": "privacy"}}
        )
        session_id = res_sess.json()["session_id"]

        res_interp = await client.post(
            "/v1/interpret",
            json={
                "session_id": session_id,
                "utterance": unique_secret_utterance,
                "manifest": MANIFEST,
            },
        )
        assert res_interp.status_code == 200

        # Create pending confirmation
        res_pend = await client.post(
            "/v1/confirmations/pending",
            json={
                "session_id": session_id,
                "action": "transfer_money",
                "params": res_interp.json()["params"],
            },
        )
        assert res_pend.status_code in (200, 201)

    # Directly inspect SQLite database tables
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        # 1. Assert table schema columns
        cursor = await db.execute("PRAGMA table_info(sessions);")
        session_cols = [row[1] for row in await cursor.fetchall()]
        assert "utterance" not in session_cols
        assert "transcript" not in session_cols

        cursor = await db.execute("PRAGMA table_info(confirmations);")
        conf_cols = [row[1] for row in await cursor.fetchall()]
        assert "utterance" not in conf_cols
        assert "transcript" not in conf_cols

        # 2. Assert unique utterance string does not appear in any row of sessions
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        session_row = await cursor.fetchone()
        assert session_row is not None
        assert unique_secret_utterance not in str(session_row)


@pytest.mark.asyncio
async def test_ndpa_cross_border_disclosure():
    """Verifies that InterpretResponse notes the LLM provider and processing region pursuant to NDPA 2023."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/v1/sessions", json={"session_id": "sess-ndpa-1"})
        res = await client.post(
            "/v1/interpret",
            json={
                "session_id": "sess-ndpa-1",
                "utterance": "abeg send 5k give David",
                "manifest": MANIFEST,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["provider"] is not None
        assert data["processing_region"] is not None
        assert "NDPA" in data["data_processing_notice"]
        assert "2023" in data["data_processing_notice"]


def test_pii_masking_logger():
    """Tests PII masking for log lines (amounts and 10+ digit runs)."""
    # 1. 10+ digit numbers (phone, account, OTP)
    sample_1 = "User requested transfer to account 0123456789 with phone 08012345678"
    masked_1 = mask_pii(sample_1)
    assert "0123456789" not in masked_1
    assert "08012345678" not in masked_1
    assert "[NUMBER MASKED]" in masked_1

    # 2. Financial amounts
    sample_2 = "Transferred ₦5,000 to David and NGN 10000 to John"
    masked_2 = mask_pii(sample_2)
    assert "5,000" not in masked_2
    assert "10000" not in masked_2
    assert "[AMOUNT MASKED]" in masked_2

    # 3. Utterance masking
    test_phrase = "abeg send 5000 naira to Mary"
    masked_utt = mask_utterance(test_phrase)
    assert "Mary" not in masked_utt
    assert "5000" not in masked_utt
    assert masked_utt == f"[UTTERANCE MASKED (len={len(test_phrase)})]"

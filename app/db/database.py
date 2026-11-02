import json
import time
import uuid
import aiosqlite
from typing import Any, Dict, Optional
from app.config import settings

async def init_db():
    """Initializes SQLite database and tables."""
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                metadata_json TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS confirmations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                action TEXT NOT NULL,
                params_json TEXT NOT NULL,
                params_hash TEXT NOT NULL,
                nonce TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL,
                spoken_prompt TEXT NOT NULL,
                token TEXT,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );
        """)
        await db.commit()

async def create_session(session_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    sid = session_id or str(uuid.uuid4())
    now = int(time.time())
    meta_str = json.dumps(metadata or {})
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (session_id, status, metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (sid, "active", meta_str, now, now)
        )
        await db.commit()
    return {
        "session_id": sid,
        "status": "active",
        "metadata": metadata or {},
        "created_at": now,
        "updated_at": now
    }

async def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "session_id": row["session_id"],
                "status": row["status"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            }

async def create_pending_confirmation(
    session_id: str,
    action: str,
    params: Dict[str, Any],
    params_hash: str,
    spoken_prompt: str,
    ttl_seconds: int = 60
) -> Dict[str, Any]:
    conf_id = str(uuid.uuid4())
    nonce = uuid.uuid4().hex
    now = int(time.time())
    expires_at = now + ttl_seconds
    params_str = json.dumps(params)
    
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT INTO confirmations (
                id, session_id, action, params_json, params_hash, nonce, status, spoken_prompt, token, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (conf_id, session_id, action, params_str, params_hash, nonce, "pending", spoken_prompt, None, now, expires_at)
        )
        await db.commit()
        
    return {
        "confirmation_id": conf_id,
        "session_id": session_id,
        "action": action,
        "params": params,
        "params_hash": params_hash,
        "nonce": nonce,
        "status": "pending",
        "spoken_prompt": spoken_prompt,
        "created_at": now,
        "expires_at": expires_at
    }

async def get_confirmation(conf_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM confirmations WHERE id = ?", (conf_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "session_id": row["session_id"],
                "action": row["action"],
                "params": json.loads(row["params_json"]),
                "params_hash": row["params_hash"],
                "nonce": row["nonce"],
                "status": row["status"],
                "spoken_prompt": row["spoken_prompt"],
                "token": row["token"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"]
            }

async def update_confirmation_status(
    conf_id: str,
    status: str,
    token: Optional[str] = None
) -> bool:
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        result = await db.execute(
            "UPDATE confirmations SET status = ?, token = coalesce(?, token) WHERE id = ?",
            (status, token, conf_id)
        )
        await db.commit()
        return result.rowcount > 0

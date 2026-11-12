import json
import time
import uuid
from typing import Any

import aiosqlite

from app.config import settings


async def init_db():
  """Initializes SQLite database and tables with WAL mode for concurrency."""
  async with aiosqlite.connect(settings.DATABASE_PATH) as db:
    await db.execute("PRAGMA journal_mode = WAL;")
    await db.execute("PRAGMA synchronous = NORMAL;")
    await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                metadata_json TEXT,
                device_key_json TEXT,
                app_id TEXT,
                locale TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
        """)
    # Safe migration for existing SQLite databases
    cursor = await db.execute("PRAGMA table_info(sessions);")
    existing_cols = {row[1] for row in await cursor.fetchall()}
    for col, col_type in [
      ("device_key_json", "TEXT"),
      ("app_id", "TEXT"),
      ("locale", "TEXT"),
    ]:
      if col not in existing_cols:
        await db.execute(f"ALTER TABLE sessions ADD COLUMN {col} {col_type};")

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
                sensitivity TEXT DEFAULT 'sensitive',
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );
        """)
    cursor_conf = await db.execute("PRAGMA table_info(confirmations);")
    existing_conf_cols = {row[1] for row in await cursor_conf.fetchall()}
    if "sensitivity" not in existing_conf_cols:
      await db.execute(
        "ALTER TABLE confirmations ADD COLUMN sensitivity TEXT DEFAULT 'sensitive';"
      )
    await db.commit()


async def create_session(
  session_id: str | None = None,
  metadata: dict[str, Any] | None = None,
  device_key: dict[str, Any] | Any | None = None,
  app_id: str | None = None,
  locale: str | None = None,
) -> dict[str, Any]:
  """Creates or updates a session. Does not fail on duplicate session_id,
  and NEVER allows replacing an existing session's device_key (H1 security fix).
  """
  sid = session_id or str(uuid.uuid4())
  now = int(time.time())
  meta_str = json.dumps(metadata or {})
  dk_dict = device_key.model_dump() if hasattr(device_key, "model_dump") else device_key
  dk_str = json.dumps(dk_dict) if dk_dict else None

  async with aiosqlite.connect(settings.DATABASE_PATH) as db:
    db.row_factory = aiosqlite.Row
    # Check if session already exists
    async with db.execute(
      "SELECT * FROM sessions WHERE session_id = ?", (sid,)
    ) as cursor:
      existing = await cursor.fetchone()

    if existing:
      # Preserve existing device_key_json (H1: NEVER overwrite device_key)
      existing_dk_json = existing["device_key_json"]
      existing_dk = json.loads(existing_dk_json) if existing_dk_json else None
      effective_dk = existing_dk if existing_dk is not None else dk_dict
      await db.execute(
        """
                UPDATE sessions SET
                    metadata_json = coalesce(?, metadata_json),
                    device_key_json = coalesce(device_key_json, ?),
                    app_id = coalesce(?, app_id),
                    locale = coalesce(?, locale),
                    updated_at = ?
                WHERE session_id = ?
                """,
        (meta_str, dk_str, app_id, locale, now, sid),
      )
      await db.commit()
      return {
        "session_id": sid,
        "status": existing["status"],
        "app_id": app_id or existing["app_id"],
        "locale": locale or existing["locale"],
        "device_key": effective_dk,
        "metadata": metadata or json.loads(existing["metadata_json"] or "{}"),
        "created_at": existing["created_at"],
        "updated_at": now,
      }

    await db.execute(
      """
            INSERT INTO sessions (
                session_id, status, metadata_json, device_key_json, app_id, locale, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
      (sid, "active", meta_str, dk_str, app_id, locale, now, now),
    )
    await db.commit()

  return {
    "session_id": sid,
    "status": "active",
    "app_id": app_id,
    "locale": locale,
    "device_key": dk_dict,
    "metadata": metadata or {},
    "created_at": now,
    "updated_at": now,
  }


SESSION_TTL_SECONDS = 86400


async def get_session(session_id: str) -> dict[str, Any] | None:
  async with aiosqlite.connect(settings.DATABASE_PATH) as db:
    db.row_factory = aiosqlite.Row
    async with db.execute(
      "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
    ) as cursor:
      row = await cursor.fetchone()
      if not row:
        return None
      # Sessions expire after 24 hours per contract v1.1
      if int(time.time()) > row["created_at"] + SESSION_TTL_SECONDS:
        return None
      row_keys = set(row.keys())
      dk_json = row["device_key_json"] if "device_key_json" in row_keys else None
      app_id = row["app_id"] if "app_id" in row_keys else None
      locale = row["locale"] if "locale" in row_keys else None
      return {
        "session_id": row["session_id"],
        "status": row["status"],
        "app_id": app_id,
        "locale": locale,
        "device_key": json.loads(dk_json) if dk_json else None,
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
      }


async def create_pending_confirmation(
  session_id: str,
  action: str,
  params: dict[str, Any],
  params_hash: str,
  spoken_prompt: str,
  ttl_seconds: int = 60,
  confirmation_id: str | None = None,
  sensitivity: str = "sensitive",
) -> dict[str, Any]:
  conf_id = confirmation_id or f"pc_{uuid.uuid4().hex[:16]}"
  nonce = uuid.uuid4().hex
  now = int(time.time())
  expires_at = now + ttl_seconds
  params_str = json.dumps(params, ensure_ascii=False)

  async with aiosqlite.connect(settings.DATABASE_PATH) as db:
    await db.execute(
      """
            INSERT INTO confirmations (
                id, session_id, action, params_json, params_hash, nonce, status, spoken_prompt, token, created_at, expires_at, sensitivity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
      (
        conf_id,
        session_id,
        action,
        params_str,
        params_hash,
        nonce,
        "pending",
        spoken_prompt,
        None,
        now,
        expires_at,
        sensitivity,
      ),
    )
    await db.commit()

  return {
    "pending_id": conf_id,
    "confirmation_id": conf_id,
    "session_id": session_id,
    "action": action,
    "params": params,
    "params_hash": params_hash,
    "nonce": nonce,
    "status": "pending",
    "spoken_prompt": spoken_prompt,
    "created_at": now,
    "expires_at": expires_at,
    "sensitivity": sensitivity,
  }


async def get_confirmation(conf_id: str) -> dict[str, Any] | None:
  async with aiosqlite.connect(settings.DATABASE_PATH) as db:
    db.row_factory = aiosqlite.Row
    async with db.execute(
      "SELECT * FROM confirmations WHERE id = ?", (conf_id,)
    ) as cursor:
      row = await cursor.fetchone()
      if not row:
        return None
      row_keys = set(row.keys())
      sens = (
        row["sensitivity"]
        if "sensitivity" in row_keys and row["sensitivity"]
        else "sensitive"
      )
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
        "expires_at": row["expires_at"],
        "sensitivity": sens,
      }


async def claim_confirmation_atomically(conf_id: str) -> bool:
  """Atomically transitions status from 'pending' to 'confirmed'.

  Guarantees single use under extreme concurrency: returns True if and only if
  this caller won the race (rowcount == 1).
  """
  async with aiosqlite.connect(settings.DATABASE_PATH) as db:
    result = await db.execute(
      "UPDATE confirmations SET status = 'confirmed' WHERE id = ? AND status = 'pending'",
      (conf_id,),
    )
    await db.commit()
    return result.rowcount == 1


async def save_confirmation_token(conf_id: str, token: str) -> bool:
  """Stores the minted JWS token for the confirmed record."""
  async with aiosqlite.connect(settings.DATABASE_PATH) as db:
    result = await db.execute(
      "UPDATE confirmations SET token = ? WHERE id = ?",
      (token, conf_id),
    )
    await db.commit()
    return result.rowcount > 0


async def update_confirmation_status(
  conf_id: str, status: str, token: str | None = None
) -> bool:
  async with aiosqlite.connect(settings.DATABASE_PATH) as db:
    result = await db.execute(
      "UPDATE confirmations SET status = ?, token = coalesce(?, token) WHERE id = ?",
      (status, token, conf_id),
    )
    await db.commit()
    return result.rowcount > 0

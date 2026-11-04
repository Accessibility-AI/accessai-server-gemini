from datetime import datetime, timezone
import re
import time
from typing import Any
import unicodedata

from app.config import settings
from app.crypto.device_auth import verify_device_auth
from app.crypto.jws_signer import hash_params, signer
from app.db.database import (
    claim_confirmation_atomically,
    create_pending_confirmation,
    get_confirmation,
    get_session,
    save_confirmation_token,
    update_confirmation_status,
)
from app.parsers.language import detect_language
from app.templates.speech import (
    build_cancelled_prompt,
    build_confirmed_prompt,
    build_transfer_confirmation_prompt,
)

# Multilingual strict confirmation phrases across 8 languages/dialects
STRICT_CONFIRMATION_PHRASES = {
    # English & Broken English / Pidgin
    "confirm",
    "i confirm",
    "confirm transfer",
    "confirm payment",
    "confirm transaction",
    "confirm send",
    "yes confirm",
    # Yoruba
    "jẹ́rìí",
    "jerii",
    "jeri",
    "mo jẹ́rìí",
    "mo jerii",
    "mo jeri",
    "mo fọwọsi",
    "mo fowosi",
    "fọwọsi",
    "fowosi",
    # Igbo
    "kwado",
    "akwadom",
    "akwadola",
    "kwado mbufe",
    # Hausa
    "tabbatar",
    "na tabbatar",
    "tabbatar da turawa",
    # French
    "confirmer",
    "je confirme",
    "confirmer le transfert",
    # German
    "bestätigen",
    "bestatigen",
    "ich bestätige",
    "ich bestatige",
    # Spanish
    "confirmar",
    "confirmo",
    "confirmar transferencia",
}

NEGATION_TERMS = {
    "not", "no", "don't", "dont", "never", "cancel", "stop", "abort",
    "kọ́", "ko", "maṣe", "mase", "mba", "ọ dịghị", "o dighi", "ba",
    "babu", "pas", "ne", "nicht", "nein", "kein"
}


def is_strict_confirmation(utterance: str) -> bool:
    """Checks user confirmation utterance against strict phrase list across all supported languages.

    NO LLM IS INVOLVED.
    Reject any utterance containing negation.
    Words like 'yes', 'oui', 'ja', 'sí', 'okay', 'proceed', or silence return False.
    """
    if not utterance or not isinstance(utterance, str):
        return False

    nfc = unicodedata.normalize("NFC", utterance).strip().lower()
    cleaned_nfc = re.sub(r"[^\w\s]", "", nfc).strip()

    words = cleaned_nfc.split()
    if any(w in NEGATION_TERMS for w in words):
        return False

    # ASCII stripped version as fallback
    ascii_clean = (
        unicodedata.normalize("NFKD", cleaned_nfc)
        .encode("ASCII", "ignore")
        .decode("utf-8")
        .strip()
    )

    return (
        cleaned_nfc in STRICT_CONFIRMATION_PHRASES
        or ascii_clean in STRICT_CONFIRMATION_PHRASES
    )


class ConfirmationService:
    @staticmethod
    async def create_pending(
        session_id: str,
        action: str,
        params: dict[str, Any],
        lang: str = "en",
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        """Creates a pending confirmation record in SQLite."""
        session = await get_session(session_id)
        if not session:
            raise LookupError("session_expired")

        p_hash = hash_params(params)

        # Build deterministic spoken prompt for the pending confirmation
        amount_kobo = params.get("amount_minor") or params.get("amount_kobo") or 0
        recipient = params.get("recipient", "")

        if action == "transfer_money" and amount_kobo and recipient:
            spoken = build_transfer_confirmation_prompt(
                int(amount_kobo), str(recipient), lang=lang
            )
        else:
            spoken = f"Please confirm {action.replace('_', ' ')}."

        record = await create_pending_confirmation(
            session_id=session_id,
            action=action,
            params=params,
            params_hash=p_hash,
            spoken_prompt=spoken,
            ttl_seconds=settings.CONFIRMATION_TOKEN_TTL_SECONDS,
            confirmation_id=confirmation_id,
        )

        iso_expires = datetime.fromtimestamp(record["expires_at"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record["params_sha256"] = p_hash
        record["expires_at_iso"] = iso_expires
        return record

    @staticmethod
    async def process_confirm(
        confirmation_id: str,
        session_id: str | None = None,
        utterance: str | None = None,
        auth: dict[str, Any] | None = None,
        method: str | None = "voice",
    ) -> tuple[bool, dict[str, Any], int]:
        """Processes confirmation strictly without LLM, verifying device authentication (v1.1).

        Returns: (success, result_dict, http_status_code)
        """
        # 1. Fetch confirmation from DB
        record = await get_confirmation(confirmation_id)
        if not record:
            return (
                False,
                {"error": {"code": "not_found", "message": "Confirmation not found"}},
                404,
            )

        # 2. Bind confirmation to session_id (Security Bug 4)
        if session_id and record["session_id"] != session_id:
            return (
                False,
                {"error": {"code": "not_found", "message": "Confirmation does not belong to session"}},
                404,
            )

        now = int(time.time())
        # 3. Check expiration (Error: 410 confirmation_expired)
        if now > record["expires_at"] or record["status"] == "expired":
            await update_confirmation_status(confirmation_id, "expired")
            return (
                False,
                {"error": {"code": "confirmation_expired", "message": "Confirmation has expired"}},
                410,
            )

        # 4. Check already confirmed or cancelled (Error: 409 confirmation_used)
        if record["status"] in ("confirmed", "cancelled"):
            return (
                False,
                {"error": {"code": "confirmation_used", "message": f"Confirmation already {record['status']}"}},
                409,
            )

        if record["status"] != "pending":
            return (
                False,
                {"error": {"code": "confirmation_used", "message": f"Confirmation status is {record['status']}"}},
                409,
            )

        # 5. Fetch session and perform device authentication verification
        session = await get_session(record["session_id"])
        if not session:
            return (
                False,
                {"error": {"code": "session_expired", "message": "Session not found or expired"}},
                401,
            )

        device_key = session.get("device_key")

        # Security Bug 2: A sensitive action (transfer_money) always requires device key and valid auth
        is_sensitive = record["action"] == "transfer_money" or settings.ENFORCE_DEVICE_AUTH or bool(device_key or auth)
        if is_sensitive:
            if not device_key:
                return (
                    False,
                    {"error": {"code": "auth_invalid", "message": "Device key not registered for session"}},
                    401,
                )
            if not auth:
                return (
                    False,
                    {"error": {"code": "auth_invalid", "message": "Device authentication required for sensitive action"}},
                    401,
                )

            # Rebuild signed payload and verify ES256 signature
            is_valid, err_reason = verify_device_auth(
                device_key=device_key,
                auth=auth,
                session_id=record["session_id"],
                pending_id=confirmation_id,
                action_id=record["action"],
                params_sha256=record["params_hash"],
            )
            if not is_valid:
                return (
                    False,
                    {"error": {"code": "auth_invalid", "message": f"Device auth verification failed: {err_reason}"}},
                    401,
                )

        # 6. Strict confirmation utterance check (NO LLM)
        if method == "voice":
            if not utterance or not is_strict_confirmation(utterance):
                return (
                    False,
                    {"error": {"code": "not_a_confirmation", "message": "Utterance is not a strict confirmation phrase"}},
                    422,
                )

        # 7. ATOMIC CLAIM UNDER CONCURRENCY (Security Bug 1)
        # Guarantees that only 1 concurrent call can ever succeed
        claimed = await claim_confirmation_atomically(confirmation_id)
        if not claimed:
            return (
                False,
                {"error": {"code": "confirmation_used", "message": "Confirmation already used"}},
                409,
            )

        # 8. Mint short-lived signed JWS token conforming to contract v1.1
        amr = [auth.get("method", "biometric")] if auth else None
        token, payload = signer.sign_token(
            session_id=record["session_id"],
            action=record["action"],
            params=record["params"],
            nonce=record["nonce"],
            ttl_seconds=settings.CONFIRMATION_TOKEN_TTL_SECONDS,
            now_ts=now,
            amr=amr,
        )

        # 9. Save minted token to SQLite
        await save_confirmation_token(confirmation_id, token)

        # 10. Spoken response in detected language
        lang = (
            detect_language(utterance)
            if utterance
            else (
                session.get("locale", "en")[:2]
                if session and session.get("locale")
                else "en"
            )
        )
        amount_kobo = record["params"].get("amount_minor") or record["params"].get("amount_kobo", 0)
        recipient = record["params"].get("recipient", "")
        spoken = build_confirmed_prompt(int(amount_kobo), str(recipient), lang=lang)

        iso_exp = datetime.fromtimestamp(payload["exp"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return (
            True,
            {
                "status": "confirmed",
                "token": token,
                "nonce": record["nonce"],
                "expires_at": iso_exp,
                "confirmation_id": confirmation_id,
                "pending_id": confirmation_id,
                "spoken_prompt": spoken,
            },
            200,
        )

    @staticmethod
    async def process_cancel(
        confirmation_id: str,
        session_id: str | None = None,
        reason: str = "user_cancelled",
        utterance: str = "",
    ) -> tuple[bool, dict[str, Any], int]:
        """Cancels a pending confirmation. Idempotent according to contract v1.1."""
        record = await get_confirmation(confirmation_id)
        if not record:
            return False, {"error": {"code": "not_found", "message": "Confirmation not found"}}, 404

        if session_id and record["session_id"] != session_id:
            return False, {"error": {"code": "not_found", "message": "Confirmation does not belong to session"}}, 404

        # If already cancelled, return success idempotently
        if record["status"] == "cancelled":
            return True, {"status": "cancelled", "confirmation_id": confirmation_id}, 204

        await update_confirmation_status(confirmation_id, "cancelled")
        lang = detect_language(utterance) if utterance else "en"
        spoken = build_cancelled_prompt(lang=lang)

        return (
            True,
            {
                "status": "cancelled",
                "confirmation_id": confirmation_id,
                "spoken_prompt": spoken,
            },
            204,
        )

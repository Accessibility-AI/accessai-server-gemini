import re
import time
import unicodedata
from typing import Any

from app.config import settings
from app.crypto.jws_signer import hash_params, signer
from app.db.database import (
    create_pending_confirmation,
    get_confirmation,
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


def is_strict_confirmation(utterance: str) -> bool:
    """Checks user confirmation utterance against strict phrase list across all supported languages.

    NO LLM IS INVOLVED.
    Words like 'yes', 'oui', 'ja', 'sí', 'okay', 'proceed', or silence return False.
    """
    if not utterance or not isinstance(utterance, str):
        return False

    nfc = unicodedata.normalize("NFC", utterance).strip().lower()
    cleaned_nfc = re.sub(r"[^\w\s]", "", nfc).strip()

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
    ) -> dict[str, Any]:
        """Creates a pending confirmation record in SQLite."""
        p_hash = hash_params(params)

        # Build deterministic spoken prompt for the pending confirmation
        if (
            action == "transfer_money"
            and "amount_kobo" in params
            and "recipient" in params
        ):
            spoken = build_transfer_confirmation_prompt(
                params["amount_kobo"], params["recipient"], lang=lang
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
        )
        return record

    @staticmethod
    async def process_confirm(
        confirmation_id: str,
        utterance: str,
    ) -> tuple[bool, dict[str, Any], int]:
        """Processes confirmation utterance strictly without LLM.

        Returns: (success, result_dict, http_status_code)
        """
        # 1. Fetch confirmation from DB
        record = await get_confirmation(confirmation_id)
        if not record:
            return False, {"error": "Confirmation not found"}, 404

        now = int(time.time())
        if now > record["expires_at"]:
            await update_confirmation_status(confirmation_id, "expired")
            return (
                False,
                {
                    "status": "expired",
                    "error": "Confirmation has expired. Please initiate the request again.",
                    "spoken_prompt": "This request has expired. Please start again.",
                },
                400,
            )

        if record["status"] != "pending":
            return (
                False,
                {
                    "status": record["status"],
                    "error": f"Confirmation is already {record['status']}",
                },
                400,
            )

        # 2. Strict phrase check across supported languages (NO LLM)
        if not is_strict_confirmation(utterance):
            return (
                False,
                {
                    "status": "rejected",
                    "error": "Confirmation phrase rejected. You must say 'confirm' to execute.",
                    "spoken_prompt": "Please say 'confirm' to proceed, or 'cancel' to abort.",
                },
                400,
            )

        # 3. Mint short-lived signed JWS token bound to action, params hash, session_id, and single-use nonce
        token, payload = signer.sign_token(
            session_id=record["session_id"],
            action=record["action"],
            params=record["params"],
            nonce=record["nonce"],
            ttl_seconds=settings.CONFIRMATION_TOKEN_TTL_SECONDS,
            now_ts=now,
        )

        # 4. Update status in database
        await update_confirmation_status(confirmation_id, "confirmed", token=token)

        # 5. Spoken response in detected language
        lang = detect_language(utterance)
        amount_kobo = record["params"].get("amount_kobo", 0)
        recipient = record["params"].get("recipient", "")
        spoken = build_confirmed_prompt(amount_kobo, recipient, lang=lang)

        return (
            True,
            {
                "status": "confirmed",
                "confirmation_id": confirmation_id,
                "token": token,
                "expires_at": payload["exp"],
                "spoken_prompt": spoken,
            },
            200,
        )

    @staticmethod
    async def process_cancel(
        confirmation_id: str,
        reason: str = "user_cancelled",
        utterance: str = "",
    ) -> tuple[bool, dict[str, Any], int]:
        """Marks pending confirmation as cancelled."""
        record = await get_confirmation(confirmation_id)
        if not record:
            return False, {"error": "Confirmation not found"}, 404

        if record["status"] != "pending":
            return (
                False,
                {
                    "status": record["status"],
                    "error": f"Confirmation is already {record['status']}",
                },
                400,
            )

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
            200,
        )

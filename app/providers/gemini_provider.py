import json
import logging
import os
from typing import Any

import httpx

from app.config import settings
from app.providers.base import BaseLLMProvider, LLMInterpretationResult, ToolCall

logger = logging.getLogger(__name__)

GEMINI_SYSTEM_PROMPT = """You are a multilingual voice intent interpreter for a blind or low-vision fintech Android app in Nigeria.
Your job is to parse the user's spoken utterance and invoke the matching tool from the available tools list.

CRITICAL RULES:
1. Utterances may be spoken in:
   - English / Nigerian English
   - Broken English / Nigerian Pidgin (e.g. "abeg send 5k give David", "wetin be my balance")
   - Yoruba (e.g. "Ẹ jọọ ẹ fi 5k ranṣẹ si David", "kí ni balance mi")
   - Igbo (e.g. "Biko ziga 5k nye David", "kedu balance m")
   - Hausa (e.g. "Don Allah tura 5k zuwa ga David", "nawa ne kudi na")
   - French (e.g. "S'il vous plaît envoyez 5k à David", "quel est mon solde")
   - German (e.g. "Bitte überweise 5k an David", "wie ist mein Kontostand")
   - Spanish (e.g. "Por favor envía 5k a David", "¿cuál es mi saldo?")
2. For transfer amounts:
   - Extract the EXACT raw text spoken by the user into 'amount_raw' (e.g. "5k", "five thousand naira", "ẹgbẹrún márùn-ún", "puku ise", "dubu biyar", "cinq mille", "fünftausend", "cinco mil", "20 grand", "1.5m", "N5000").
   - If you can determine the numeric value in Naira, provide it in 'amount_numeric_estimate' (e.g. 5000 for 5k). If unsure, leave it null.
   - NEVER invent or guess an amount. If the user did not say an amount, do NOT guess.
3. For recipient:
   - Extract ONLY the explicitly spoken recipient name into 'recipient' (e.g. "David", "Mama", "Chinedu", "Sarah").
   - NEVER guess or invent a recipient. If none was specified, leave it empty or null.
4. If the request does not clearly match any available tool or is completely outside the banking app scope, do not call any tool.
"""

GEMINI_TYPE_MAP = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def sanitize_schema_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively formats JSON schema / OpenAPI schema to be compliant with Google Gemini API."""
    if not isinstance(schema, dict):
        return schema

    cleaned: dict[str, Any] = {}

    for k, v in schema.items():
        if k == "type" and isinstance(v, str):
            cleaned["type"] = GEMINI_TYPE_MAP.get(v.lower(), v.upper())
        elif k == "properties" and isinstance(v, dict):
            cleaned["properties"] = {
                prop_name: sanitize_schema_for_gemini(prop_val)
                for prop_name, prop_val in v.items()
            }
        elif k == "items" and isinstance(v, dict):
            cleaned["items"] = sanitize_schema_for_gemini(v)
        elif k == "required" and isinstance(v, list):
            cleaned["required"] = v
        elif k == "description" and isinstance(v, str):
            cleaned["description"] = v
        elif k == "enum" and isinstance(v, list):
            cleaned["enum"] = v

    if "type" not in cleaned:
        cleaned["type"] = "OBJECT"

    return cleaned


class GeminiProvider(BaseLLMProvider):
    """Google Gemini LLM Provider implementing tool calling and intent interpretation."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        if api_key is not None:
            self.api_key = api_key
        else:
            self.api_key = (
                settings.GEMINI_API_KEY
                or os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY")
                or ""
            )
        self.model = model or settings.GEMINI_MODEL or "gemini-flash-lite-latest"
        self.api_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        )

    async def interpret(
        self,
        utterance: str,
        manifest: list[dict[str, Any]],
        screen_descriptor: str | None = None,
        dialog_state: dict[str, Any] | None = None,
    ) -> LLMInterpretationResult:
        if not self.api_key:
            raise ValueError(
                "Gemini API key not configured. Set GEMINI_API_KEY or GOOGLE_API_KEY in .env or environment."
            )

        # 1. Convert manifest to Gemini function declarations
        function_declarations = []
        for action in manifest:
            params = action.get("parameters", {"type": "object", "properties": {}})
            function_declarations.append(
                {
                    "name": action["name"],
                    "description": action.get("description", ""),
                    "parameters": sanitize_schema_for_gemini(params),
                }
            )

        # 2. Build user context
        user_context_parts = [f"Utterance: {utterance}"]
        if screen_descriptor:
            user_context_parts.append(f"Current Screen: {screen_descriptor}")
        if dialog_state:
            user_context_parts.append(f"Dialog Context: {json.dumps(dialog_state)}")

        # 3. Build Gemini payload
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": "\n".join(user_context_parts)}],
                }
            ],
            "system_instruction": {
                "parts": [{"text": GEMINI_SYSTEM_PROMPT}],
            },
            "generation_config": {
                "temperature": 0.0,
            },
        }

        if function_declarations:
            payload["tools"] = [{"function_declarations": function_declarations}]
            payload["tool_config"] = {
                "function_calling_config": {
                    "mode": "AUTO",
                }
            }

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPStatusError as e:
                error_detail = ""
                try:
                    error_detail = e.response.text
                except Exception:
                    pass
                logger.error(f"Gemini API error ({e.response.status_code}): {error_detail}")
                raise RuntimeError(
                    f"Gemini API request failed with status {e.response.status_code}: {error_detail}"
                ) from e
            except httpx.RequestError as e:
                logger.error(f"Gemini connection error: {e}")
                raise RuntimeError(f"Failed to connect to Google Gemini API: {e}") from e

        # 4. Extract candidate parts
        tool_calls_out: list[ToolCall] = []
        text_content = ""

        candidates = data.get("candidates", [])
        if candidates:
            first_candidate = candidates[0]
            content = first_candidate.get("content", {})
            parts = content.get("parts", [])

            for part in parts:
                if "functionCall" in part:
                    fc = part["functionCall"]
                    fn_name = fc.get("name", "")
                    fn_args = fc.get("args", {})
                    # Ensure args is a dictionary
                    if isinstance(fn_args, str):
                        try:
                            fn_args = json.loads(fn_args)
                        except Exception:
                            fn_args = {}
                    tool_calls_out.append(ToolCall(name=fn_name, arguments=fn_args))
                elif "text" in part:
                    text_content += part.get("text", "")

        # Gemini outputs high quality structured function calls.
        confidence = 0.96 if tool_calls_out else 0.5

        return LLMInterpretationResult(
            tool_calls=tool_calls_out,
            confidence=confidence,
            text_content=text_content or None,
            raw_response=data,
        )

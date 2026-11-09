"""Request/response models for contract v1.1.

Descriptions and examples here feed the live OpenAPI docs (/docs) and the exported
contracts in contracts/v1/ (regenerate with `python scripts/export_contracts.py`).
Fields marked deprecated are kept for older clients only and are not part of the contract.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_LEGACY = {"deprecated": True}

SDK_TRANSFER_ACTION = {
  "id": "transfer_money",
  "name": "send money",
  "description": "Send money to a saved beneficiary",
  "sensitivity": "sensitive",
  "requires_confirmation": True,
  "params": [
    {
      "name": "recipient",
      "type": "string",
      "description": "Beneficiary name as the user said it",
      "required": True,
    },
    {
      "name": "amount_minor",
      "type": "amount_minor",
      "description": "Amount in kobo",
      "required": True,
    },
  ],
  "examples": ["send 5000 naira to David"],
}
EXAMPLE_SESSION_ID = "s_8f2c41d0a7b94e31"
EXAMPLE_PENDING_ID = "pc_3b9e7a10c4d2"
EXAMPLE_PARAMS_SHA256 = (
  "b729a280eabf6c63b6a6b889bfa1b26d4dad23286f2e3a36b98028e35879cd47"
)


class ErrorDetail(BaseModel):
  code: str = Field(
    description="Machine-readable error code; see the error table in API_CONTRACT.md",
    examples=["not_a_confirmation"],
  )
  message: str = Field(
    description="Human-readable detail for logs; never shown to users"
  )


class ErrorResponse(BaseModel):
  """Body of every non-2xx response."""

  error: ErrorDetail


class DeviceKey(BaseModel):
  kid: str = Field(description="Key ID; confirm requests must sign with the same kid")
  alg: str = Field(default="ES256", description="Only ES256 (ECDSA P-256) is accepted")
  public_key: str = Field(
    ...,
    description="Base64 X.509 SubjectPublicKeyInfo DER encoded ECDSA public key",
  )
  attestation_chain: list[str] | None = Field(
    default=None,
    description="Optional list of base64 DER X.509 certificates proving hardware backing",
  )


class SessionCreateRequest(BaseModel):
  model_config = ConfigDict(
    json_schema_extra={
      "examples": [
        {
          "app_id": "accesspay-demo",
          "locale": "en-NG",
          "device_key": {
            "kid": "device-1",
            "alg": "ES256",
            "public_key": "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE…",
          },
        }
      ]
    }
  )

  # No session_id: the server always generates it (a client-chosen id could be used to
  # attach a device key to someone else's session). A sent session_id is ignored.
  app_id: str | None = Field(default=None, description="Host app's public identifier")
  locale: str | None = Field(
    default=None, description="BCP 47 locale hint, e.g. en-NG, yo-NG, ha-NG"
  )
  device_key: DeviceKey | None = Field(
    default=None,
    description="Android Keystore public key. Required to confirm sensitive actions",
  )
  metadata: dict[str, Any] | None = None


class SessionResponse(BaseModel):
  model_config = ConfigDict(
    json_schema_extra={
      "examples": [
        {
          "session_id": EXAMPLE_SESSION_ID,
          "expires_at": "2026-11-10T12:00:00Z",
          "status": "active",
          "app_id": "accesspay-demo",
          "locale": "en-NG",
        }
      ]
    }
  )

  session_id: str
  expires_at: str = Field(description="ISO 8601 UTC; sessions last 24 hours")
  status: str = "active"
  app_id: str | None = None
  locale: str | None = None
  device_key: DeviceKey | None = None
  metadata: dict[str, Any] = Field(default_factory=dict)
  created_at: int | None = None
  updated_at: int | None = None


class ManifestParameterProperty(BaseModel):
  type: str
  description: str | None = None


class ManifestAction(BaseModel):
  name: str
  description: str
  parameters: dict[str, Any] | None = None


class InterpretRequest(BaseModel):
  model_config = ConfigDict(
    json_schema_extra={
      "examples": [
        {
          "session_id": EXAMPLE_SESSION_ID,
          "text": "Send 5,000 to David",
          "locale": "en-NG",
          "screen": {
            "screen_id": "home",
            "title": "Home",
            "summary": None,
            "elements": [],
          },
          "actions": [SDK_TRANSFER_ACTION],
          "dialog": {
            "pending_action_id": None,
            "filled_params": {},
            "missing_params": [],
            "awaiting": "none",
            "last_system_message": None,
          },
        }
      ]
    }
  )

  session_id: str = Field(description="From POST /v1/sessions")
  text: str | None = Field(default=None, description="The user's utterance")
  utterance: str | None = Field(default=None, json_schema_extra=_LEGACY)
  actions: list[dict[str, Any]] | None = Field(
    default=None,
    description="The host's allowlist. The ONLY actions that may be returned",
  )
  manifest: list[dict[str, Any]] | None = Field(default=None, json_schema_extra=_LEGACY)
  screen_descriptor: str | None = Field(default=None, json_schema_extra=_LEGACY)
  screen: Any = Field(default=None, description="Current screen descriptor (optional)")
  dialog: dict[str, Any] | list[Any] | None = Field(
    default=None, description="What AccessAI is in the middle of, if anything"
  )
  dialog_state: dict[str, Any] | list[Any] | None = Field(
    default=None, json_schema_extra=_LEGACY
  )
  locale: str | None = Field(default=None, description="BCP 47 locale hint")


class InterpretResponse(BaseModel):
  model_config = ConfigDict(
    json_schema_extra={
      "examples": [
        {
          "request_id": "r_5d1e9c",
          "outcome": "action",
          "action_id": "transfer_money",
          "params": {"recipient": "David", "amount_minor": 500000},
          "missing_params": [],
          "control": None,
          "message": None,
          "confidence": 0.93,
        }
      ]
    }
  )

  request_id: str
  outcome: str = Field(
    default="action", description='"action" | "clarify" | "unsupported" | "control"'
  )
  action_id: str | None = Field(
    default=None, description="For action: always one of request.actions[].id"
  )
  params: dict[str, Any] = Field(
    default_factory=dict,
    description="Only params the user actually said; amount_minor is an integer in kobo",
  )
  missing_params: list[str] = Field(default_factory=list)
  control: str | None = Field(
    default=None,
    description="For control: cancel | stop | repeat | next | previous | help | describe_screen",
  )
  message: str | None = Field(
    default=None, description="For clarify/unsupported: short spoken text"
  )
  confidence: float = Field(description="0..1; the SDK treats low values as unclear")

  # Backward compatibility fields (not part of the contract)
  type: str | None = Field(default=None, json_schema_extra=_LEGACY)
  intent: str | None = Field(default=None, json_schema_extra=_LEGACY)
  spoken_prompt: str | None = Field(default=None, json_schema_extra=_LEGACY)
  clarification_reason: str | None = Field(default=None, json_schema_extra=_LEGACY)
  language: str = Field(default="en", json_schema_extra=_LEGACY)
  provider: str | None = Field(default=None, json_schema_extra=_LEGACY)
  processing_region: str | None = Field(default=None, json_schema_extra=_LEGACY)
  data_processing_notice: str | None = Field(default=None, json_schema_extra=_LEGACY)


class PendingConfirmationRequest(BaseModel):
  model_config = ConfigDict(
    json_schema_extra={
      "examples": [
        {
          "session_id": EXAMPLE_SESSION_ID,
          "action_id": "transfer_money",
          "params": {"amount_minor": 500000, "recipient": "ben_001"},
        }
      ]
    }
  )

  session_id: str
  action_id: str | None = Field(default=None, description="The action being confirmed")
  action: str | None = Field(default=None, json_schema_extra=_LEGACY)
  params: dict[str, Any] = Field(
    description="RESOLVED values (beneficiary id, not name); exactly what the bank will receive"
  )
  # No sensitivity field: every pending confirmation requires device auth. A sent
  # sensitivity is ignored.


class PendingConfirmationResponse(BaseModel):
  model_config = ConfigDict(
    json_schema_extra={
      "examples": [
        {
          "pending_id": EXAMPLE_PENDING_ID,
          "params_sha256": EXAMPLE_PARAMS_SHA256,
          "expires_at": "2026-11-09T12:01:00Z",
        }
      ]
    }
  )

  pending_id: str
  params_sha256: str = Field(
    description="Lowercase hex SHA-256 of the canonical JSON of params. The SDK must check it matches its own"
  )
  expires_at: str = Field(description="ISO 8601 UTC; 60 seconds by default")

  # Backward compatibility fields (not part of the contract)
  confirmation_id: str | None = Field(default=None, json_schema_extra=_LEGACY)
  session_id: str | None = Field(default=None, json_schema_extra=_LEGACY)
  action: str | None = Field(default=None, json_schema_extra=_LEGACY)
  params: dict[str, Any] | None = Field(default=None, json_schema_extra=_LEGACY)
  status: str = Field(default="pending", json_schema_extra=_LEGACY)
  spoken_prompt: str | None = Field(default=None, json_schema_extra=_LEGACY)


class DeviceAuth(BaseModel):
  method: str = Field(
    default="biometric",
    description="Device unlock method: 'biometric' or 'device_credential'. Becomes the token's amr claim",
  )
  kid: str = Field(..., description="Key ID matching session device_key.kid")
  alg: str = Field(default="ES256", description="Signature algorithm (ES256)")
  signature: str = Field(
    ...,
    description="Base64 DER ECDSA signature over accessai-auth-v1|{session_id}|{pending_id}|{action_id}|{params_sha256}",
  )


class ConfirmRequest(BaseModel):
  model_config = ConfigDict(
    json_schema_extra={
      "examples": [
        {
          "session_id": EXAMPLE_SESSION_ID,
          "method": "voice",
          "utterance": "confirm transfer",
          "auth": {
            "method": "biometric",
            "kid": "device-1",
            "alg": "ES256",
            "signature": "MEUCIQD…",
          },
        }
      ]
    }
  )

  session_id: str = Field(description="The session that created the confirmation")
  pending_id: str | None = Field(default=None, json_schema_extra=_LEGACY)
  confirmation_id: str | None = Field(default=None, json_schema_extra=_LEGACY)
  method: str | None = Field(
    default="voice",
    description='"voice" (utterance is checked against a strict phrase list) or "touch"',
  )
  utterance: str | None = Field(
    default=None, description='For voice: e.g. "confirm" or "confirm transfer"'
  )
  auth: DeviceAuth | None = Field(
    default=None, description="Required for sensitive actions"
  )


class ConfirmResponse(BaseModel):
  model_config = ConfigDict(
    json_schema_extra={
      "examples": [
        {
          "status": "confirmed",
          "token": "eyJhbGciOiJFZERTQSIsImtpZCI6ImFjY2Vzc2FpLWZseS0xIn0.eyJpc3MiOiJhY2Nlc3NhaSJ9.c2ln",
          "nonce": "8b0e4f2a9c1d",
          "expires_at": "2026-11-09T12:01:00Z",
        }
      ]
    }
  )

  status: str
  token: str = Field(
    description="Compact JWS (EdDSA). Verify against /.well-known/jwks.json before executing"
  )
  nonce: str = Field(description="Equals the token's jti; use as the idempotency key")
  expires_at: str = Field(description="Token expiry, ISO 8601 UTC (at most 60 s)")

  # Backward compatibility fields (not part of the contract)
  confirmation_id: str | None = Field(default=None, json_schema_extra=_LEGACY)
  spoken_prompt: str | None = Field(default=None, json_schema_extra=_LEGACY)


class CancelRequest(BaseModel):
  model_config = ConfigDict(
    json_schema_extra={"examples": [{"session_id": EXAMPLE_SESSION_ID}]}
  )

  session_id: str = Field(description="The session that created the confirmation")
  reason: str | None = "user_cancelled"


class CancelResponse(BaseModel):
  status: str
  confirmation_id: str
  spoken_prompt: str

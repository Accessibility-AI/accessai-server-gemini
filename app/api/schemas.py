from typing import Any

from pydantic import BaseModel, Field


class DeviceKey(BaseModel):
    kid: str
    alg: str = "ES256"
    public_key: str = Field(
        ...,
        description="Base64 X.509 SubjectPublicKeyInfo DER encoded ECDSA public key",
    )
    attestation_chain: list[str] | None = Field(
        default=None,
        description="Optional list of base64 DER X.509 certificates proving hardware backing",
    )


class SessionCreateRequest(BaseModel):
    session_id: str | None = None
    app_id: str | None = None
    locale: str | None = None
    device_key: DeviceKey | None = None
    metadata: dict[str, Any] | None = None


class SessionResponse(BaseModel):
    session_id: str
    status: str
    app_id: str | None = None
    locale: str | None = None
    device_key: DeviceKey | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: int
    updated_at: int


class ManifestParameterProperty(BaseModel):
    type: str
    description: str | None = None


class ManifestAction(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] | None = None


class InterpretRequest(BaseModel):
    session_id: str
    utterance: str
    manifest: list[dict[str, Any]] = Field(
        ..., description="Allowed actions exposed by the host Android app"
    )
    screen_descriptor: str | None = None
    screen: dict[str, Any] | list[Any] | str | None = None
    dialog_state: dict[str, Any] | None = None


class InterpretResponse(BaseModel):
    type: str  # "action" or "clarification"
    intent: str | None = None
    params: dict[str, Any] | None = None
    confidence: float
    spoken_prompt: str
    clarification_reason: str | None = None
    language: str = "en"
    provider: str | None = None
    processing_region: str | None = None
    data_processing_notice: str | None = None


class PendingConfirmationRequest(BaseModel):
    session_id: str
    action: str
    params: dict[str, Any]


class PendingConfirmationResponse(BaseModel):
    confirmation_id: str
    session_id: str
    action: str
    params: dict[str, Any]
    status: str
    spoken_prompt: str
    expires_at: int


class DeviceAuth(BaseModel):
    method: str = Field(
        default="biometric",
        description="Device unlock method: 'biometric' or 'device_credential'",
    )
    kid: str = Field(..., description="Key ID matching session device_key.kid")
    alg: str = Field(default="ES256", description="Signature algorithm (ES256)")
    signature: str = Field(
        ...,
        description="Base64 DER ECDSA signature over accessai-auth-v1|{session_id}|{pending_id}|{action_id}|{params_sha256}",
    )


class ConfirmRequest(BaseModel):
    session_id: str | None = None
    pending_id: str | None = None
    confirmation_id: str | None = None
    method: str | None = "voice"
    utterance: str | None = None
    auth: DeviceAuth | None = None


class ConfirmResponse(BaseModel):
    status: str
    confirmation_id: str
    token: str
    expires_at: int
    spoken_prompt: str


class CancelRequest(BaseModel):
    reason: str | None = "user_cancelled"


class CancelResponse(BaseModel):
    status: str
    confirmation_id: str
    spoken_prompt: str

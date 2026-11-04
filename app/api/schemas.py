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
    expires_at: str
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
    session_id: str
    text: str | None = None
    utterance: str | None = None
    actions: list[dict[str, Any]] | None = None
    manifest: list[dict[str, Any]] | None = None
    screen_descriptor: str | None = None
    screen: Any = None
    dialog: dict[str, Any] | list[Any] | None = None
    dialog_state: dict[str, Any] | list[Any] | None = None
    locale: str | None = None


class InterpretResponse(BaseModel):
    request_id: str
    outcome: str = "action"  # "action" | "clarify" | "unsupported" | "control"
    action_id: str | None = None  # MUST be one of request.actions[].id
    params: dict[str, Any] = Field(default_factory=dict)
    missing_params: list[str] = Field(default_factory=list)
    control: str | None = None  # cancel | stop | repeat | next | previous | help | describe_screen
    message: str | None = None  # for clarify/unsupported
    confidence: float

    # Backward compatibility fields
    type: str | None = None
    intent: str | None = None
    spoken_prompt: str | None = None
    clarification_reason: str | None = None
    language: str = "en"
    provider: str | None = None
    processing_region: str | None = None
    data_processing_notice: str | None = None


class PendingConfirmationRequest(BaseModel):
    session_id: str
    action_id: str | None = None
    action: str | None = None
    params: dict[str, Any]


class PendingConfirmationResponse(BaseModel):
    pending_id: str
    params_sha256: str
    expires_at: str

    # Backward compatibility fields
    confirmation_id: str | None = None
    session_id: str | None = None
    action: str | None = None
    params: dict[str, Any] | None = None
    status: str = "pending"
    spoken_prompt: str | None = None


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
    token: str
    nonce: str
    expires_at: str

    # Backward compatibility fields
    confirmation_id: str | None = None
    spoken_prompt: str | None = None


class CancelRequest(BaseModel):
    session_id: str | None = None
    reason: str | None = "user_cancelled"


class CancelResponse(BaseModel):
    status: str
    confirmation_id: str
    spoken_prompt: str

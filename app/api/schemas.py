from typing import Any

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    session_id: str | None = None
    metadata: dict[str, Any] | None = None


class SessionResponse(BaseModel):
    session_id: str
    status: str
    metadata: dict[str, Any]
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
    dialog_state: dict[str, Any] | None = None


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


class ConfirmRequest(BaseModel):
    utterance: str


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

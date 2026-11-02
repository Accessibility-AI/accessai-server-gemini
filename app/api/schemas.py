from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class SessionCreateRequest(BaseModel):
    session_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class SessionResponse(BaseModel):
    session_id: str
    status: str
    metadata: Dict[str, Any]
    created_at: int
    updated_at: int

class ManifestParameterProperty(BaseModel):
    type: str
    description: Optional[str] = None

class ManifestAction(BaseModel):
    name: str
    description: str
    parameters: Optional[Dict[str, Any]] = None

class InterpretRequest(BaseModel):
    session_id: str
    utterance: str
    manifest: List[Dict[str, Any]] = Field(
        ...,
        description="Allowed actions exposed by the host Android app"
    )
    screen_descriptor: Optional[str] = None
    dialog_state: Optional[Dict[str, Any]] = None

class PendingConfirmationRequest(BaseModel):
    session_id: str
    action: str
    params: Dict[str, Any]

class PendingConfirmationResponse(BaseModel):
    confirmation_id: str
    session_id: str
    action: str
    params: Dict[str, Any]
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
    reason: Optional[str] = "user_cancelled"

class CancelResponse(BaseModel):
    status: str
    confirmation_id: str
    spoken_prompt: str

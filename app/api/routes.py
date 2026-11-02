from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import (
    CancelRequest,
    CancelResponse,
    ConfirmRequest,
    ConfirmResponse,
    InterpretRequest,
    PendingConfirmationRequest,
    PendingConfirmationResponse,
    SessionCreateRequest,
    SessionResponse,
)
from app.crypto.jws_signer import signer
from app.db.database import (
    create_session,
    get_session,
)
from app.providers import get_llm_provider
from app.services.confirmation import ConfirmationService
from app.services.interpreter import InterpreterService, InterpretResponse

router = APIRouter()


def get_interpreter_service() -> InterpreterService:
    provider = get_llm_provider()
    return InterpreterService(provider=provider)


@router.post("/v1/sessions", response_model=SessionResponse, status_code=201)
async def create_new_session(req: SessionCreateRequest):
    return await create_session(session_id=req.session_id, metadata=req.metadata)


@router.post("/v1/interpret", response_model=InterpretResponse)
async def interpret_utterance(
    req: InterpretRequest,
    service: Annotated[InterpreterService, Depends(get_interpreter_service)],
):
    # Ensure session exists
    session = await get_session(req.session_id)
    if not session:
        # Create session if not explicitly initialized
        await create_session(session_id=req.session_id)

    return await service.interpret_utterance(
        utterance=req.utterance,
        manifest=req.manifest,
        screen_descriptor=req.screen_descriptor,
        dialog_state=req.dialog_state,
    )


@router.post(
    "/v1/confirmations/pending",
    response_model=PendingConfirmationResponse,
    status_code=201,
)
async def create_pending_action(req: PendingConfirmationRequest):
    return await ConfirmationService.create_pending(
        session_id=req.session_id,
        action=req.action,
        params=req.params,
    )


@router.post("/v1/confirmations/{id}/confirm", response_model=ConfirmResponse)
async def confirm_action(id: str, req: ConfirmRequest):
    success, result, status_code = await ConfirmationService.process_confirm(
        confirmation_id=id,
        utterance=req.utterance,
    )
    if not success:
        raise HTTPException(status_code=status_code, detail=result)
    return result


@router.post("/v1/confirmations/{id}/cancel", response_model=CancelResponse)
async def cancel_action(id: str, req: CancelRequest):
    success, result, status_code = await ConfirmationService.process_cancel(
        confirmation_id=id,
        reason=req.reason or "user_cancelled",
    )
    if not success:
        raise HTTPException(status_code=status_code, detail=result)
    return result


@router.get("/.well-known/jwks.json")
async def get_jwks():
    """Serves the public key in JWKS format for token verification by the host backend."""
    return signer.get_jwks()

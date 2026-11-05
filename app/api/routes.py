from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from app.api.schemas import (
    CancelRequest,
    ConfirmRequest,
    ConfirmResponse,
    InterpretRequest,
    InterpretResponse,
    PendingConfirmationRequest,
    PendingConfirmationResponse,
    SessionCreateRequest,
    SessionResponse,
)
from app.config import settings
from app.crypto.jws_signer import signer
from app.db.database import (
    create_session,
    get_session,
)
from app.providers import get_llm_provider
from app.services.confirmation import ConfirmationService
from app.services.interpreter import InterpreterService, validate_defence_in_depth

router = APIRouter()

_SERVICE_CACHE: dict[str, InterpreterService] = {}


def clear_service_cache():
    _SERVICE_CACHE.clear()


def get_interpreter_service() -> InterpreterService:
    p_name = settings.LLM_PROVIDER.lower()
    if p_name not in _SERVICE_CACHE:
        provider = get_llm_provider(p_name)
        _SERVICE_CACHE[p_name] = InterpreterService(provider=provider)
    return _SERVICE_CACHE[p_name]


@router.post("/v1/sessions", response_model=SessionResponse, status_code=200)
async def create_new_session(req: SessionCreateRequest):
    sess = await create_session(
        session_id=req.session_id,
        metadata=req.metadata,
        device_key=req.device_key,
        app_id=req.app_id,
        locale=req.locale,
    )
    expires_at_iso = datetime.fromtimestamp(sess["created_at"] + 86400, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    sess["expires_at"] = expires_at_iso
    return sess


@router.post("/v1/interpret", response_model=InterpretResponse)
async def interpret_utterance(
    req: InterpretRequest,
    service: Annotated[InterpreterService, Depends(get_interpreter_service)],
):
    text = req.text or req.utterance
    if not text:
        return JSONResponse(
            status_code=400,
            content={
                "error": {"code": "bad_request", "message": "Missing text or utterance"}
            },
        )

    actions = req.actions or req.manifest or []
    dialog = req.dialog or req.dialog_state

    # Defence in depth (v1.1): Reject 400 bad_request on 10+ digit runs or screen element values
    is_valid, err_reason = validate_defence_in_depth(
        utterance=text,
        screen=req.screen,
        screen_descriptor=req.screen_descriptor,
        dialog_state=dialog,
    )
    if not is_valid:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "bad_request", "message": err_reason}},
        )

    # Security: Ensure session exists (reject unknown sessions with 401 session_expired)
    session = await get_session(req.session_id)
    if not session:
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "session_expired",
                    "message": "Unknown or expired session_id",
                }
            },
        )

    try:
        return await service.interpret_utterance(
            utterance=text,
            manifest=actions,
            screen_descriptor=req.screen_descriptor,
            screen=req.screen,
            dialog_state=dialog,
            locale=req.locale,
        )
    except ValueError as e:
        err_msg = str(e)
        if "API key not configured" in err_msg:
            return JSONResponse(
                status_code=500,
                content={"error": {"code": "server", "message": err_msg}},
            )
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "bad_request", "message": err_msg}},
        )
    except TimeoutError as e:
        return JSONResponse(
            status_code=504,
            content={"error": {"code": "server", "message": str(e)}},
        )
    except Exception as e:  # noqa: BLE001
        err_msg = str(e)
        if "API key not configured" in err_msg:
            return JSONResponse(
                status_code=500,
                content={"error": {"code": "server", "message": err_msg}},
            )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "server",
                    "message": f"Interpretation failed: {err_msg}",
                }
            },
        )


@router.post(
    "/v1/confirmations", response_model=PendingConfirmationResponse, status_code=200
)
@router.post(
    "/v1/confirmations/pending",
    response_model=PendingConfirmationResponse,
    status_code=200,
)
async def create_pending_confirmation_endpoint(req: PendingConfirmationRequest):
    # Security Bug 3: Reject unknown sessions with session_expired (401)
    session = await get_session(req.session_id)
    if not session:
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "session_expired",
                    "message": "Unknown or expired session_id",
                }
            },
        )

    action_name = req.action_id or req.action
    if not action_name:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "bad_request",
                    "message": "Missing action or action_id",
                }
            },
        )

    record = await ConfirmationService.create_pending(
        session_id=req.session_id,
        action=action_name,
        params=req.params,
        sensitivity=req.sensitivity or "sensitive",
    )
    return {
        "pending_id": record["pending_id"],
        "params_sha256": record["params_sha256"],
        "expires_at": record["expires_at_iso"],
        "confirmation_id": record["pending_id"],
        "session_id": req.session_id,
        "action": action_name,
        "params": req.params,
        "status": "pending",
        "spoken_prompt": record.get("spoken_prompt", ""),
    }


@router.post("/v1/confirmations/{id}/confirm", response_model=ConfirmResponse)
async def confirm_action(id: str, req: ConfirmRequest):
    # Security Bug 4: Require session_id in confirm request body
    if not req.session_id:
        return JSONResponse(
            status_code=404,
            content={
                "error": {"code": "not_found", "message": "session_id is required"}
            },
        )

    auth_dict = req.auth.model_dump() if req.auth else None
    success, result, status_code = await ConfirmationService.process_confirm(
        confirmation_id=id,
        session_id=req.session_id,
        utterance=req.utterance,
        auth=auth_dict,
        method=req.method or "voice",
    )
    if not success:
        return JSONResponse(status_code=status_code, content=result)
    return result


@router.post("/v1/confirm", response_model=ConfirmResponse)
@router.post("/v1/confirmations/confirm", response_model=ConfirmResponse)
async def confirm_action_direct(req: ConfirmRequest):
    conf_id = req.confirmation_id or req.pending_id
    if not conf_id:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "bad_request",
                    "message": "Missing confirmation_id or pending_id",
                }
            },
        )

    if not req.session_id:
        return JSONResponse(
            status_code=404,
            content={
                "error": {"code": "not_found", "message": "session_id is required"}
            },
        )

    auth_dict = req.auth.model_dump() if req.auth else None
    success, result, status_code = await ConfirmationService.process_confirm(
        confirmation_id=conf_id,
        session_id=req.session_id,
        utterance=req.utterance,
        auth=auth_dict,
        method=req.method or "voice",
    )
    if not success:
        return JSONResponse(status_code=status_code, content=result)
    return result


@router.post("/v1/confirmations/{id}/cancel", status_code=204)
async def cancel_action(id: str, req: CancelRequest | None = None):
    cancel_req = req or CancelRequest()
    if not cancel_req.session_id:
        return JSONResponse(
            status_code=400,
            content={
                "error": {"code": "bad_request", "message": "session_id is required"}
            },
        )
    success, result, status_code = await ConfirmationService.process_cancel(
        confirmation_id=id,
        session_id=cancel_req.session_id,
        reason=cancel_req.reason or "user_cancelled",
    )
    if not success:
        return JSONResponse(status_code=status_code, content=result)
    return Response(status_code=204)


@router.get("/.well-known/jwks.json")
async def get_jwks():
    """Serves the public key in JWKS format for token verification by the host backend."""
    return signer.get_jwks()

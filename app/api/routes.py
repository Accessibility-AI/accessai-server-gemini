from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from app.api.schemas import (
  CancelRequest,
  ConfirmRequest,
  ConfirmResponse,
  ErrorResponse,
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

SDK_TAG = "SDK (contract v1.1)"
BANK_TAG = "Bank backend"

_ERROR_TEXT = {
  "bad_request": "Missing or invalid field",
  "session_expired": "Unknown or expired session_id",
  "auth_invalid": "Sensitive action without a valid device signature",
  "not_found": "Unknown pending_id, or it belongs to another session",
  "confirmation_used": "Already confirmed or cancelled",
  "confirmation_expired": "Confirmed after expires_at",
  "not_a_confirmation": "Voice utterance is not a strict confirmation phrase",
  "server": "Model or server failure",
}
_ERROR_STATUS = {
  "bad_request": 400,
  "session_expired": 401,
  "auth_invalid": 401,
  "not_found": 404,
  "confirmation_used": 409,
  "confirmation_expired": 410,
  "not_a_confirmation": 422,
  "server": 500,
}


def _errors(*codes: str) -> dict:
  """OpenAPI `responses` entries for the contract error codes an endpoint can return."""
  by_status: dict[int, list[str]] = {}
  for code in codes:
    by_status.setdefault(_ERROR_STATUS[code], []).append(code)
  return {
    status: {
      "model": ErrorResponse,
      "description": "; ".join(f"`{c}`: {_ERROR_TEXT[c]}" for c in group),
      "content": {
        "application/json": {
          "example": {"error": {"code": group[0], "message": _ERROR_TEXT[group[0]]}}
        }
      },
    }
    for status, group in by_status.items()
  }


_SERVICE_CACHE: dict[str, InterpreterService] = {}


def clear_service_cache():
  _SERVICE_CACHE.clear()


def get_interpreter_service() -> InterpreterService:
  p_name = settings.LLM_PROVIDER.lower()
  if p_name not in _SERVICE_CACHE:
    provider = get_llm_provider(p_name)
    _SERVICE_CACHE[p_name] = InterpreterService(provider=provider)
  return _SERVICE_CACHE[p_name]


@router.post(
  "/v1/sessions",
  response_model=SessionResponse,
  status_code=200,
  tags=[SDK_TAG],
  summary="Start a session",
  responses=_errors("bad_request"),
)
async def create_new_session(req: SessionCreateRequest):
  """Creates a session (valid 24 hours). Register the device's Android Keystore public key
  here (`device_key`): it is required later to confirm sensitive actions."""
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


@router.post(
  "/v1/interpret",
  response_model=InterpretResponse,
  tags=[SDK_TAG],
  summary="Interpret one utterance",
  responses=_errors("bad_request", "session_expired", "server"),
)
async def interpret_utterance(
  req: InterpretRequest,
  service: Annotated[InterpreterService, Depends(get_interpreter_service)],
):
  """Turns what the user said into a structured suggestion. **Never executes anything.**

  `action_id` is always one of `request.actions[].id`. Params the user didn't say are left
  out, never guessed. `amount_minor` is an integer in kobo, parsed deterministically.
  Utterances containing 10+ digit runs (e.g. account numbers) are rejected with `bad_request`.
  """
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
  "/v1/confirmations",
  response_model=PendingConfirmationResponse,
  status_code=200,
  tags=[SDK_TAG],
  summary="Create a pending confirmation",
  responses=_errors("bad_request", "session_expired"),
)
@router.post(
  "/v1/confirmations/pending",
  response_model=PendingConfirmationResponse,
  status_code=200,
  include_in_schema=False,  # legacy alias; removal tracked in #25
)
async def create_pending_confirmation_endpoint(req: PendingConfirmationRequest):
  """Called after the SDK has read the details back to the user. `params` are the
  **resolved** values the bank will receive. The SDK must refuse to continue if
  `params_sha256` differs from its own hash (reference vector in API_CONTRACT.md)."""
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


@router.post(
  "/v1/confirmations/{id}/confirm",
  response_model=ConfirmResponse,
  tags=[SDK_TAG],
  summary="Confirm and get a signed token",
  responses=_errors(
    "auth_invalid",
    "session_expired",
    "not_found",
    "confirmation_used",
    "confirmation_expired",
    "not_a_confirmation",
  ),
)
async def confirm_action(id: str, req: ConfirmRequest):
  """Single use. For `voice`, the utterance must be a strict confirmation phrase
  ("confirm", "confirm transfer"); "yes", "okay" and anything with a negation are rejected.
  Sensitive actions also need `auth`: an ES256 signature from the session's device key over
  `accessai-auth-v1|{session_id}|{pending_id}|{action_id}|{params_sha256}`."""
  # Security Bug 4: Require session_id in confirm request body
  if not req.session_id:
    return JSONResponse(
      status_code=404,
      content={"error": {"code": "not_found", "message": "session_id is required"}},
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


# Legacy aliases, hidden from the docs; removal tracked in #25
@router.post("/v1/confirm", response_model=ConfirmResponse, include_in_schema=False)
@router.post(
  "/v1/confirmations/confirm", response_model=ConfirmResponse, include_in_schema=False
)
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
      content={"error": {"code": "not_found", "message": "session_id is required"}},
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


@router.post(
  "/v1/confirmations/{id}/cancel",
  status_code=204,
  tags=[SDK_TAG],
  summary="Cancel a pending confirmation",
  responses=_errors("bad_request", "not_found", "confirmation_used"),
)
async def cancel_action(id: str, req: CancelRequest | None = None):
  """Idempotent: cancelling twice returns 204. Returns `confirmation_used` if it was
  already confirmed."""
  cancel_req = req or CancelRequest()
  if not cancel_req.session_id:
    return JSONResponse(
      status_code=400,
      content={"error": {"code": "bad_request", "message": "session_id is required"}},
    )
  success, result, status_code = await ConfirmationService.process_cancel(
    confirmation_id=id,
    session_id=cancel_req.session_id,
    reason=cancel_req.reason or "user_cancelled",
  )
  if not success:
    return JSONResponse(status_code=status_code, content=result)
  return Response(status_code=204)


@router.get(
  "/.well-known/jwks.json",
  tags=[BANK_TAG],
  summary="Public keys for verifying tokens",
  responses={
    200: {
      "content": {
        "application/json": {
          "example": {
            "keys": [
              {
                "kty": "OKP",
                "crv": "Ed25519",
                "kid": "accessai-fly-1",
                "use": "sig",
                "alg": "EdDSA",
                "x": "UrEEBnzChlPMqcKtwWItJr3W6uIdOFaTIgvspefMt48",
              }
            ]
          }
        }
      }
    }
  },
)
async def get_jwks():
  """Before executing a sensitive action the bank backend must: verify the token's EdDSA
  signature against these keys (matching `kid`) and its `exp`; check `aid` is the action
  it is about to run; recompute the canonical SHA-256 of the exact params it received and
  compare to `ph`; and reject any `jti` it has seen before."""
  return signer.get_jwks()

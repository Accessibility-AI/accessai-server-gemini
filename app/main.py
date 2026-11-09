import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse

from app.api.routes import router
from app.config import production_config_errors, settings
from app.db.database import init_db
from app.providers.gemini_provider import close_shared_client

DEMO_HTML = Path(__file__).parent.parent / "demo" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
  # Refuse to serve with unsafe production settings (e.g. the mock LLM, missing API key)
  config_errors = production_config_errors(settings)
  if config_errors:
    raise RuntimeError("Refusing to start: " + " ".join(config_errors))

  # Initialize DB tables with WAL mode
  await init_db()
  yield
  # Gracefully shut down HTTP client connection pool
  await close_shared_client()


API_DESCRIPTION = """
AI service for the AccessAI Android SDK: a voice accessibility layer for blind and
low-vision users of fintech apps. Implements **contract v1.1**.

The service suggests actions and issues short-lived signed confirmation tokens.
**It never moves money.** The bank verifies each token before executing anything.

- Contract (behaviour rules, hashing, token claims): `contracts/v1/API_CONTRACT.md`
- Machine-readable spec: `contracts/v1/openapi.json` and the per-model JSON schemas beside it

**SDK flow:** `POST /v1/sessions` → `POST /v1/interpret` (repeat per utterance) →
`POST /v1/confirmations` → `POST /v1/confirmations/{id}/confirm` (or `/cancel`).
The host's bank backend verifies the returned token with `GET /.well-known/jwks.json`.

Every non-2xx response has the body `{"error": {"code": "...", "message": "..."}}`.
"""

OPENAPI_TAGS = [
  {"name": "SDK (contract v1.1)", "description": "Endpoints the Android SDK calls."},
  {
    "name": "Bank backend",
    "description": "Used by the host's backend to verify confirmation tokens.",
  },
  {"name": "Ops", "description": "Health checks."},
]

app = FastAPI(
  title="AccessAI Server",
  description=API_DESCRIPTION,
  version="1.1.0",
  openapi_tags=OPENAPI_TAGS,
  lifespan=lifespan,
)


def custom_openapi() -> dict:
  """Drops FastAPI's automatic 422 entries: validation errors are 400 bad_request here."""
  if app.openapi_schema:
    return app.openapi_schema
  schema = get_openapi(
    title=app.title,
    version=app.version,
    description=app.description,
    routes=app.routes,
    tags=app.openapi_tags,
  )
  for path_item in schema.get("paths", {}).values():
    for operation in path_item.values():
      default_422 = operation.get("responses", {}).get("422", {})
      if "HTTPValidationError" in json.dumps(default_422):
        del operation["responses"]["422"]
  for name in ("HTTPValidationError", "ValidationError"):
    schema.get("components", {}).get("schemas", {}).pop(name, None)
  app.openapi_schema = schema
  return schema


app.openapi = custom_openapi


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
  """Schema errors use the contract envelope (400 bad_request), not FastAPI's 422 default."""
  first = exc.errors()[0] if exc.errors() else {}
  field = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
  message = (
    f"{field}: {first.get('msg', 'invalid')}" if field else "Invalid request body"
  )
  return JSONResponse(
    status_code=400, content={"error": {"code": "bad_request", "message": message}}
  )


app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)

app.include_router(router)


@app.get("/health", tags=["Ops"], summary="Health check")
async def health_check():
  return {"status": "healthy", "provider": settings.LLM_PROVIDER}


@app.get("/", include_in_schema=False)
@app.get("/demo", include_in_schema=False)
async def serve_demo():
  if DEMO_HTML.exists():
    return FileResponse(DEMO_HTML, media_type="text/html")
  return JSONResponse(
    {"message": "AccessAI Gemini Server running. Demo page not found."}
  )

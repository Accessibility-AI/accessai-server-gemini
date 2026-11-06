from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api.routes import router
from app.config import settings
from app.db.database import init_db
from app.providers.gemini_provider import close_shared_client

DEMO_HTML = Path(__file__).parent.parent / "demo" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
  # Initialize DB tables with WAL mode
  await init_db()
  yield
  # Gracefully shut down HTTP client connection pool
  await close_shared_client()


app = FastAPI(
  title="AccessAI Server",
  description="AccessAI AI Service: Developer-approved voice accessibility layer for blind and low-vision fintech Android apps",
  version="1.1.0",
  lifespan=lifespan,
)

app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
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

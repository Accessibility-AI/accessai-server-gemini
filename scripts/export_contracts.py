"""Export the API contract (OpenAPI + per-model JSON schemas) to contracts/v1/.

  python scripts/export_contracts.py          # regenerate the files
  python scripts/export_contracts.py --check  # exit 1 if the files are out of date

tests/test_contracts_export.py runs the check in CI, so the exported contract can't drift
from the code.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.api.schemas import (
  CancelRequest,
  CancelResponse,
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
from app.main import app

OUT_DIR = ROOT / "contracts" / "v1"


def build_contracts() -> dict[str, str]:
  """Returns {filename: file contents} for every exported contract file."""
  app.openapi_schema = None  # rebuild from the current routes
  documents = {
    # Full OpenAPI 3.1 specification
    "openapi.json": app.openapi(),
    # Individual JSON Schemas for Android SDK contract testing
    "session_create_request.json": SessionCreateRequest.model_json_schema(),
    "session_response.json": SessionResponse.model_json_schema(),
    "interpret_request.json": InterpretRequest.model_json_schema(),
    "interpret_response.json": InterpretResponse.model_json_schema(),
    "pending_confirmation_request.json": PendingConfirmationRequest.model_json_schema(),
    "pending_confirmation_response.json": PendingConfirmationResponse.model_json_schema(),
    "confirm_request.json": ConfirmRequest.model_json_schema(),
    "confirm_response.json": ConfirmResponse.model_json_schema(),
    "cancel_request.json": CancelRequest.model_json_schema(),
    "cancel_response.json": CancelResponse.model_json_schema(),
    "error_response.json": ErrorResponse.model_json_schema(),
  }
  return {
    name: json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    for name, doc in documents.items()
  }


def stale_files(out_dir: Path = OUT_DIR) -> list[str]:
  """Names of exported files that are missing or differ from the current code."""
  stale = []
  for name, content in build_contracts().items():
    path = out_dir / name
    current = (
      path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
    )
    if current != content:
      stale.append(name)
  return stale


def export_contracts(out_dir: Path = OUT_DIR) -> None:
  os.makedirs(out_dir, exist_ok=True)
  for name, content in build_contracts().items():
    (out_dir / name).write_text(content, encoding="utf-8", newline="\n")
    print(f"Exported contracts/v1/{name}")


if __name__ == "__main__":
  if "--check" in sys.argv:
    stale = stale_files()
    if stale:
      print("Out of date: " + ", ".join(stale))
      print("Run: python scripts/export_contracts.py")
      sys.exit(1)
    print("contracts/v1 is up to date")
  else:
    export_contracts()

"""The exported contract (contracts/v1/) must match the code, and the docs must match the contract."""

import importlib.util
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.main import app

ROOT = Path(__file__).resolve().parent.parent
SDK_PATHS = {
  "/v1/sessions",
  "/v1/interpret",
  "/v1/confirmations",
  "/v1/confirmations/{id}/confirm",
  "/v1/confirmations/{id}/cancel",
  "/.well-known/jwks.json",
}


def _exporter():
  spec = importlib.util.spec_from_file_location(
    "export_contracts", ROOT / "scripts" / "export_contracts.py"
  )
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_exported_contracts_are_up_to_date():
  stale = _exporter().stale_files()
  assert not stale, (
    f"contracts/v1 is out of date ({', '.join(stale)}). "
    "Run: python scripts/export_contracts.py"
  )


def test_openapi_lists_only_contract_routes():
  paths = set(app.openapi()["paths"])
  assert paths == SDK_PATHS | {"/health"}


def test_every_sdk_operation_is_documented():
  spec = app.openapi()
  for path in SDK_PATHS:
    for method, op in spec["paths"][path].items():
      assert op.get("summary"), f"{method} {path} has no summary"
      assert op.get("description"), f"{method} {path} has no description"
      assert "422" not in op["responses"] or "not_a_confirmation" in str(
        op["responses"]["422"]
      ), f"{method} {path} still documents FastAPI's default 422"


async def test_validation_errors_use_contract_envelope():
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
    res = await c.post("/v1/interpret", json={"text": "hi"})
  assert res.status_code == 400
  assert res.json()["error"]["code"] == "bad_request"
  assert "session_id" in res.json()["error"]["message"]

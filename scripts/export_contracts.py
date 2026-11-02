import json
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath("."))

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
from app.main import app
from app.services.interpreter import InterpretResponse


def export_contracts():
    out_dir = os.path.join("contracts", "v1")
    os.makedirs(out_dir, exist_ok=True)

    # 1. Full OpenAPI 3.1 specification
    openapi_spec = app.openapi()
    with open(os.path.join(out_dir, "openapi.json"), "w", encoding="utf-8") as f:
        json.dump(openapi_spec, f, indent=2)
    print("Exported contracts/v1/openapi.json")

    # 2. Individual JSON Schemas for Android SDK contract testing
    schemas = {
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
    }

    for filename, schema in schemas.items():
        with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2)
        print(f"Exported contracts/v1/{filename}")


if __name__ == "__main__":
    export_contracts()

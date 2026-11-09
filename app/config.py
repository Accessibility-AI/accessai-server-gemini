from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
  model_config = SettingsConfigDict(env_file=".env", extra="ignore")

  APP_ENV: str = "production"
  DEBUG: bool = False
  PORT: int = 8000
  HOST: str = "0.0.0.0"

  # SQLite Database
  DATABASE_PATH: str = "voice_intent.db"

  # LLM Provider configuration
  LLM_PROVIDER: Literal["gemini", "groq", "anthropic", "mock"] = "gemini"
  LLM_REGION: str = "US"  # Region of processing for NDPA 2023 disclosures
  GEMINI_API_KEY: str = ""
  GEMINI_MODEL: str = "gemini-flash-lite-latest"
  GROQ_API_KEY: str = ""
  GROQ_MODEL: str = "openai/gpt-oss-120b"
  ANTHROPIC_API_KEY: str = ""
  ANTHROPIC_MODEL: str = "claude-3-5-haiku-20241022"

  # Security & Device Auth (v1.1)
  ENFORCE_DEVICE_AUTH: bool = False  # Enforce device key on all sensitive actions

  # Confidence threshold for intent routing
  CONFIDENCE_THRESHOLD: float = 0.85

  # Token expiration in seconds
  CONFIRMATION_TOKEN_TTL_SECONDS: int = 60

  # Ed25519 Key configuration (base64 private key or path)
  ED25519_PRIVATE_KEY_B64: str = ""
  JWKS_KID: str = "voice-intent-key-1"

  # Browser access. The Android SDK doesn't use CORS; only list origins of web pages that
  # must call this API from a browser, as a JSON list: '["https://demo.example.com"]'.
  # Empty (default) = no cross-origin browser access. Credentials are never allowed.
  CORS_ALLOW_ORIGINS: list[str] = []

  # Serve the voice studio demo page at / and /demo. Off by default: every visitor's
  # requests use this deployment's LLM quota.
  ENABLE_DEMO_PAGE: bool = False


_PROVIDER_API_KEY_FIELDS = {
  "gemini": "GEMINI_API_KEY",
  "groq": "GROQ_API_KEY",
  "anthropic": "ANTHROPIC_API_KEY",
}


def production_config_errors(cfg: Settings) -> list[str]:
  """Lists settings that are unsafe for a production deployment.

  Checked at app startup (not import) so tests and tooling can still import settings.
  A missing ED25519_PRIVATE_KEY_B64 is already refused by the JWS signer in production.
  """
  if cfg.APP_ENV != "production" or cfg.DEBUG:
    return []

  errors: list[str] = []
  if cfg.LLM_PROVIDER == "mock":
    errors.append(
      "LLM_PROVIDER=mock is not allowed in production; "
      "set it to gemini, groq or anthropic with a valid API key."
    )
  else:
    key_field = _PROVIDER_API_KEY_FIELDS[cfg.LLM_PROVIDER]
    if not getattr(cfg, key_field):
      errors.append(f"{key_field} must be set when LLM_PROVIDER={cfg.LLM_PROVIDER}.")
  return errors


settings = Settings()

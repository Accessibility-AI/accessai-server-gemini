import pytest

from app.config import Settings, production_config_errors, settings
from app.main import app, lifespan


def _prod(**overrides) -> Settings:
  values = {
    "APP_ENV": "production",
    "DEBUG": False,
    "LLM_PROVIDER": "gemini",
    "GEMINI_API_KEY": "key",
    "GROQ_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
  }
  values.update(overrides)
  return Settings(**values)


def test_valid_production_config_has_no_errors():
  assert production_config_errors(_prod()) == []


def test_mock_provider_refused_in_production():
  errors = production_config_errors(_prod(LLM_PROVIDER="mock"))
  assert len(errors) == 1
  assert "mock" in errors[0]


@pytest.mark.parametrize(
  ("provider", "key_field"),
  [
    ("gemini", "GEMINI_API_KEY"),
    ("groq", "GROQ_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
  ],
)
def test_missing_api_key_for_active_provider_refused(provider, key_field):
  errors = production_config_errors(_prod(LLM_PROVIDER=provider, **{key_field: ""}))
  assert errors == [f"{key_field} must be set when LLM_PROVIDER={provider}."]


def test_checks_skipped_outside_production():
  assert (
    production_config_errors(_prod(APP_ENV="development", LLM_PROVIDER="mock")) == []
  )
  assert production_config_errors(_prod(DEBUG=True, LLM_PROVIDER="mock")) == []


async def test_app_refuses_to_start_with_mock_in_production(monkeypatch):
  monkeypatch.setattr(settings, "APP_ENV", "production")
  monkeypatch.setattr(settings, "DEBUG", False)
  monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
  with pytest.raises(RuntimeError, match="Refusing to start"):
    async with lifespan(app):
      pass

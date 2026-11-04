import pytest

from app.config import settings
from app.providers import clear_provider_cache


@pytest.fixture(autouse=True)
def default_mock_provider(monkeypatch, request):
    """Defaults to MockLLMProvider for unit tests so tests do not exhaust Gemini rate limits,

    unless the test is specifically testing the live Gemini provider.
    """
    clear_provider_cache()
    # If the test is in test_gemini_provider.py, keep gemini as provider
    if "test_gemini_provider" in request.node.nodeid:
        return

    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")

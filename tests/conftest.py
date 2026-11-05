import pytest

from app.api.routes import clear_service_cache
from app.config import settings
from app.db.database import init_db
from app.providers import clear_provider_cache


@pytest.fixture(autouse=True)
async def isolate_database(monkeypatch, tmp_path):
    """Isolates SQLite database per test to prevent state pollution and H1 key mismatch."""
    test_db = str(tmp_path / "test.db")
    monkeypatch.setattr(settings, "DATABASE_PATH", test_db)
    await init_db()


@pytest.fixture(autouse=True)
def default_mock_provider(monkeypatch, request):
    """Defaults to MockLLMProvider for unit tests so tests do not exhaust Gemini rate limits,

    unless the test is specifically testing the live Gemini provider.
    """
    clear_provider_cache()
    clear_service_cache()
    # If the test is in test_gemini_provider.py, keep gemini as provider
    if "test_gemini_provider" in request.node.nodeid:
        return

    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")

from app.config import settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import BaseLLMProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.groq_provider import GroqProvider
from app.providers.mock_provider import MockLLMProvider

_PROVIDER_CACHE: dict[str, BaseLLMProvider] = {}


def get_llm_provider(name: str | None = None) -> BaseLLMProvider:
  provider_name = (name or settings.LLM_PROVIDER).lower()
  if provider_name in _PROVIDER_CACHE:
    return _PROVIDER_CACHE[provider_name]

  if provider_name == "gemini":
    provider = GeminiProvider()
  elif provider_name == "groq":
    provider = GroqProvider()
  elif provider_name == "anthropic":
    provider = AnthropicProvider()
  elif provider_name == "mock":
    provider = MockLLMProvider()
  else:
    raise ValueError(f"Unknown LLM provider: {provider_name}")

  _PROVIDER_CACHE[provider_name] = provider
  return provider


def clear_provider_cache():
  """Clears cached providers, useful for testing configuration changes."""
  _PROVIDER_CACHE.clear()

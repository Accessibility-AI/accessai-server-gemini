from app.config import settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import BaseLLMProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.groq_provider import GroqProvider
from app.providers.mock_provider import MockLLMProvider


def get_llm_provider(name: str | None = None) -> BaseLLMProvider:
    provider_name = (name or settings.LLM_PROVIDER).lower()
    if provider_name == "gemini":
        return GeminiProvider()
    elif provider_name == "groq":
        return GroqProvider()
    elif provider_name == "anthropic":
        return AnthropicProvider()
    elif provider_name == "mock":
        return MockLLMProvider()
    else:
        raise ValueError(f"Unknown LLM provider: {provider_name}")

from ..config import Settings
from .base import LLMClient


def create_client(settings: Settings) -> LLMClient:
    if settings.provider == "anthropic":
        from .anthropic_client import AnthropicClient

        return AnthropicClient(settings.anthropic_model, settings.anthropic_effort)
    from .ollama_client import OllamaClient

    return OllamaClient(settings.ollama_model, settings.ollama_host, settings.ollama_num_ctx)

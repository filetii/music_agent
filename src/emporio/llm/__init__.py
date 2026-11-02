from ..config import Settings
from .base import LLMClient
from .ollama_client import OllamaClient


def create_client(settings: Settings) -> LLMClient:
    return OllamaClient(settings.ollama_model, settings.ollama_host, settings.ollama_num_ctx)

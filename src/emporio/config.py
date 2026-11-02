"""Runtime settings read from environment variables (and an optional .env file)."""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORE_TIMEZONE = "America/Campo_Grande"


@dataclass(frozen=True)
class Settings:
    provider: str
    anthropic_model: str
    anthropic_effort: str
    ollama_model: str
    ollama_host: str
    ollama_num_ctx: int
    reference_date: date
    max_tool_steps: int
    data_dir: Path
    handoffs_path: Path


def load_settings(provider: str | None = None) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    provider = provider or os.getenv("LLM_PROVIDER", "anthropic")
    if provider not in ("anthropic", "ollama"):
        raise ValueError(f"LLM_PROVIDER must be 'anthropic' or 'ollama', got {provider!r}")
    return Settings(
        provider=provider,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"),
        anthropic_effort=os.getenv("ANTHROPIC_EFFORT", "low"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:8b"),
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        ollama_num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "8192")),
        reference_date=date.fromisoformat(os.getenv("REFERENCE_DATE", "2026-03-25")),
        max_tool_steps=int(os.getenv("MAX_TOOL_STEPS", "6")),
        data_dir=PROJECT_ROOT / "data",
        handoffs_path=PROJECT_ROOT / "handoffs.jsonl",
    )

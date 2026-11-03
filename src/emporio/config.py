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
    ollama_model: str
    ollama_host: str
    ollama_num_ctx: int
    ollama_think: bool
    reference_date: date
    max_tool_steps: int
    data_dir: Path
    handoffs_path: Path


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    return Settings(
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:8b"),
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        ollama_num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "8192")),
        ollama_think=os.getenv("OLLAMA_THINK", "false").lower() == "true",
        reference_date=date.fromisoformat(os.getenv("REFERENCE_DATE", "2026-03-25")),
        max_tool_steps=int(os.getenv("MAX_TOOL_STEPS", "6")),
        data_dir=PROJECT_ROOT / "data",
        handoffs_path=PROJECT_ROOT / "handoffs.db",
    )

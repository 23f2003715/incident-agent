from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./incident-agent.db")
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    ollama_timeout_seconds: float = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "20"))
    ollama_max_retries: int = int(os.getenv("OLLAMA_MAX_RETRIES", "3"))
    approval_timeout_seconds: int = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "900"))


settings = Settings()

"""
Every setting the system reads, in one typed object.

Configuration is loaded once at import and validated immediately, so a missing
key or a malformed number fails at startup with a clear message rather than
halfway through a call. Nothing else in the codebase reads os.environ directly;
if a setting is needed somewhere, it belongs here first.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Typed view of .env, with the defaults that were chosen by measurement."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Language models -----------------------------------------------------
    gemini_api_key: str = ""
    # Runs during a call. Picked on first-token latency, not on capability.
    gemini_model: str = "gemini-3.1-flash-lite"
    # Runs after a call, where a few extra seconds cost nothing.
    gemini_deep_model: str = "gemini-3.5-flash"
    # Newer models deliberate before answering, which roughly triples the wait
    # before the first word. Zero disables it. Use -1 for the model's default.
    gemini_thinking_budget: int = 0

    groq_api_key: str = ""
    groq_asr_model: str = "whisper-large-v3-turbo"
    # Stands in for Gemini when the free tier throttles, so a rate limit
    # interrupts a sentence rather than ending a call.
    groq_llm_model: str = "llama-3.3-70b-versatile"

    deepgram_api_key: str = ""
    deepgram_model: str = "nova-2"

    # --- Speech --------------------------------------------------------------
    tts_voice_en: str = "en-US-AriaNeural"
    tts_voice_fil: str = "fil-PH-BlessicaNeural"
    tts_voice_id: str = "id-ID-GadisNeural"

    # --- Retrieval -----------------------------------------------------------
    # Hosted embeddings, because the local models small enough to run here do
    # not cover Tagalog. See scripts/benchmark_embeddings.py.
    embedding_provider: str = "gemini"
    embedding_model: str = "gemini-embedding-001"
    embedding_dims: int = 768
    embedding_local_model: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )

    retrieval_top_k: int = 5
    # Below this score the agent says it does not know, rather than answering
    # from a weak match. This is the main defence against invented answers.
    retrieval_min_score: float = 0.35

    # --- Live nudges ---------------------------------------------------------
    nudge_min_confidence: float = 0.6
    nudge_cooldown_seconds: int = 45
    nudge_expiry_seconds: int = 120
    audio_chunk_ms: int = 500

    # --- Service -------------------------------------------------------------
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    log_level: str = "INFO"
    escalation_webhook_url: str = ""

    # --- Local paths ---------------------------------------------------------
    model_cache_dir: str = ".cache/models"

    @field_validator("embedding_provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        allowed = {"gemini", "local"}
        cleaned = value.strip().lower()
        if cleaned not in allowed:
            raise ValueError(
                f"EMBEDDING_PROVIDER must be one of {sorted(allowed)}, got {value!r}"
            )
        return cleaned

    @field_validator("log_level")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        cleaned = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if cleaned not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return cleaned

    # --- Derived paths -------------------------------------------------------
    # Kept as properties so they are always absolute. A relative cache path
    # would otherwise resolve against whatever directory a script was launched
    # from, and models would be downloaded more than once.

    @property
    def root(self) -> Path:
        return PROJECT_ROOT

    @property
    def cache_path(self) -> Path:
        path = Path(self.model_cache_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def data_path(self) -> Path:
        return PROJECT_ROOT / "data"

    @property
    def kb_path(self) -> Path:
        path = PROJECT_ROOT / "data" / "kb"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def results_path(self) -> Path:
        path = PROJECT_ROOT / "results"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def missing_keys(self) -> list[str]:
        """Which credentials are absent, so callers can fail with a useful message."""
        required = {
            "GEMINI_API_KEY": self.gemini_api_key,
            "GROQ_API_KEY": self.groq_api_key,
            "DEEPGRAM_API_KEY": self.deepgram_api_key,
        }
        return [name for name, value in required.items() if not value.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once and reuse them."""
    loaded = Settings()

    # The model download library reads this from the environment rather than
    # taking it as an argument, so it has to be set before anything imports it.
    # Pointing it at the project keeps large files off the system drive.
    os.environ.setdefault("HF_HOME", str(loaded.cache_path))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    return loaded


settings = get_settings()

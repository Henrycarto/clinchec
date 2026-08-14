"""Runtime configuration for Clinchec Scan.

Everything is environment-driven so the same image runs locally under
docker-compose and on ECS Fargate with no code changes.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    service_name: str = "clinchec-scan"
    environment: str = "local"
    log_level: str = "info"

    # --- NLP -------------------------------------------------------------
    spacy_model: str = "en_core_web_sm"
    # When set, this takes precedence: a fine-tuned clinical NER pipeline
    # (e.g. scispaCy's en_core_sci_md or an in-house model artifact path).
    spacy_clinical_model: str | None = None
    # Notes longer than this are truncated before parsing; a 30k-char note is
    # almost always a paste error and blows up the O(n) matcher passes.
    max_note_chars: int = 30_000

    # --- Justification drafting ------------------------------------------
    justification_enabled: bool = False
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"
    openai_timeout_seconds: float = 20.0

    # --- Downstream ------------------------------------------------------
    database_url: str | None = None
    redis_url: str | None = None
    live_service_url: str = "http://live:8000"

    # --- HTTP ------------------------------------------------------------
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def active_spacy_model(self) -> str:
        return self.spacy_clinical_model or self.spacy_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

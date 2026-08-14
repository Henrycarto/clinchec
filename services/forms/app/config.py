"""Runtime configuration for Clinchec Forms."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    service_name: str = "clinchec-forms"
    environment: str = "local"
    log_level: str = "info"

    database_url: str | None = None
    scan_service_url: str = "http://scan:8000"
    live_service_url: str = "http://live:8000"

    # --- Export / storage -------------------------------------------------
    forms_export_bucket: str = "clinchec-dev-exports"
    aws_region: str = "us-east-1"
    #: Presigned download links are short-lived; an exported PA packet is PHI.
    export_url_ttl_seconds: int = 900

    # --- Submission -------------------------------------------------------
    #: Real portal/EDI submission is gated behind per-payer credentials and a
    #: signed BAA. Until those exist for an environment, submission produces a
    #: downloadable packet instead of transmitting.
    submission_enabled: bool = False
    fax_gateway_url: str | None = None
    x12_clearinghouse_url: str | None = None

    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

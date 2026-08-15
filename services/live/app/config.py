"""Runtime configuration for Clinchec Live."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    service_name: str = "clinchec-live"
    environment: str = "local"
    log_level: str = "info"

    # --- Persistence -----------------------------------------------------
    database_url: str = "postgresql+asyncpg://clinchec:clinchec_dev_password@postgres:5432/clinchec"
    redis_url: str = "redis://redis:6379/0"

    # --- Queue -----------------------------------------------------------
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"
    payer_poll_interval_minutes: int = 360

    # --- Crawling --------------------------------------------------------
    payer_http_timeout_seconds: float = 30.0
    payer_user_agent: str = "ClinchecLive/0.1 (+https://clinchec.com/bot)"
    # Politeness: minimum seconds between requests to the same host. Payer
    # portals are not rate-limit friendly and a ban costs us the data source.
    payer_request_delay_seconds: float = 1.5
    payer_max_pages_per_run: int = 40
    respect_robots_txt: bool = True

    # How many consecutive COMPLETE crawls a rule may be absent from before it
    # is retired and stops being served. Only complete crawls count — a capped,
    # failed, or offline-seed run says nothing about whether a policy still
    # exists — so with the nightly schedule this is roughly three days of a
    # policy being genuinely gone.
    #
    # The trade is asymmetric. Retiring too eagerly withdraws criteria a
    # practice is relying on; retiring too slowly leaves a rule citing a dead
    # URL, which is what this exists to fix and is the milder failure. Three
    # sits on the cautious side of that on purpose.
    rule_retire_after_missed_crawls: int = 3

    aetna_portal_base_url: str = "https://www.aetna.com"
    bcbs_portal_base_url: str = "https://www.bcbs.com"
    uhc_portal_base_url: str = "https://www.uhcprovider.com"

    # When true (the default for local dev), adapters skip the network and
    # load their published-criteria seed set instead.
    offline_seed_mode: bool = True
    # Load seed rules at startup when the rules table is empty, so a fresh
    # deployment is useful before its first crawl completes.
    seed_rules_on_startup: bool = True

    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def portal_base_url(self, slug: str) -> str:
        return {
            "aetna": self.aetna_portal_base_url,
            "bcbs": self.bcbs_portal_base_url,
            "uhc": self.uhc_portal_base_url,
        }.get(slug, "")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

"""Wire contract for Clinchec Live."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class PlanType(StrEnum):
    COMMERCIAL = "commercial"
    MEDICARE_ADVANTAGE = "medicare_advantage"
    MEDICAID = "medicaid"
    EXCHANGE = "exchange"
    ANY = "any"


class CrawlStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class PolicyDocument(BaseModel):
    """A payer policy page or PDF discovered by the crawler."""

    payer_slug: str
    url: str
    title: str
    plan_type: PlanType = PlanType.ANY
    effective_date: date | None = None


class RuleDraft(BaseModel):
    """A criteria set parsed out of one policy document.

    This is what an adapter produces. `rules_sync` diffs drafts against what is
    already stored and only writes real changes, so an unchanged nightly crawl
    is a no-op rather than a rewrite of the whole table.
    """

    payer_slug: str
    cpt_code: str
    icd10_codes: list[str] = Field(default_factory=list)
    plan_type: PlanType = PlanType.ANY
    requires_pa: bool = True
    criteria_text: str
    required_duration_weeks: int | None = None
    required_conservative_care: list[str] = Field(default_factory=list)
    required_imaging: list[str] = Field(default_factory=list)
    source_url: str | None = None
    effective_date: date | None = None

    def checksum(self) -> str:
        """Stable digest of the adjudicating fields only.

        Deliberately excludes `source_url` and `effective_date`: a payer
        re-publishing the same criteria at a new URL is not a rule change, and
        treating it as one would spam every practice with false alerts.
        """
        import hashlib

        payload = "|".join(
            [
                self.payer_slug,
                self.cpt_code,
                ",".join(sorted(self.icd10_codes)),
                self.plan_type.value,
                str(self.requires_pa),
                str(self.required_duration_weeks),
                ",".join(sorted(self.required_conservative_care)),
                ",".join(sorted(self.required_imaging)),
                " ".join(self.criteria_text.split()),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PayerRuleResult(BaseModel):
    """A stored rule as served to Clinchec Scan and Forms."""

    payer_slug: str
    payer_name: str
    cpt_code: str
    icd10_codes: list[str] = Field(default_factory=list)
    plan_type: PlanType = PlanType.ANY
    requires_pa: bool = True
    criteria_text: str
    required_duration_weeks: int | None = None
    required_conservative_care: list[str] = Field(default_factory=list)
    required_imaging: list[str] = Field(default_factory=list)
    source_url: str | None = None
    effective_date: date | None = None
    last_verified_at: datetime
    # How stale the record is. The UI shows this — a rule verified 4 hours ago
    # carries different weight than one last seen 6 weeks ago.
    staleness_hours: float = 0.0


class PayerSummary(BaseModel):
    slug: str
    display_name: str
    portal_base_url: str | None = None
    rule_count: int = 0
    last_crawled_at: datetime | None = None
    last_crawl_status: CrawlStatus | None = None


class CrawlResult(BaseModel):
    payer_slug: str
    status: CrawlStatus
    rules_seen: int = 0
    rules_changed: int = 0
    pages_fetched: int = 0
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None


class RuleChange(BaseModel):
    """One field-level difference between a stored rule and a fresh draft."""

    field: str
    previous: object | None = None
    current: object | None = None


class SyncReport(BaseModel):
    payer_slug: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    changes: list[dict] = Field(default_factory=list)


class HealthResult(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    database_reachable: bool
    broker_reachable: bool
    registered_payers: list[str]
    offline_seed_mode: bool

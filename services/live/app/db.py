"""Async persistence for Clinchec Live.

Schema is owned by `infra/sql/001_init.sql` — this module maps onto it rather
than declaring its own DDL, so there is exactly one definition of the tables.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.schemas import CrawlResult, PayerRuleResult, PayerSummary, PlanType, RuleDraft

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            echo=False,
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database engine not initialised; call init_engine() first.")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping(settings: Settings) -> bool:
    try:
        engine = init_engine(settings)
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 — health check must not raise
        logger.warning("Database ping failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

_RULE_SELECT = """
SELECT
    p.slug                       AS payer_slug,
    p.display_name               AS payer_name,
    r.cpt_code,
    r.icd10_codes,
    r.plan_type,
    r.requires_pa,
    r.criteria_text,
    r.required_duration_weeks,
    r.required_conservative_care,
    r.required_imaging,
    r.source_url,
    r.effective_date,
    r.last_verified_at
FROM payer_rules r
JOIN payers p ON p.id = r.payer_id
"""


async def get_rule(
    payer_slug: str,
    cpt_code: str,
    plan_type: str | None = None,
) -> PayerRuleResult | None:
    """Fetch one payer's criteria for one procedure.

    When the caller does not name a plan type, an exact plan match is preferred
    over the payer's `any`-plan default, because a Medicare Advantage member is
    adjudicated against different criteria than a commercial one.
    """
    # `CAST(:x AS text)` rather than `:x::text` — SQLAlchemy's `text()` does not
    # bind a parameter when a colon immediately follows the name, so the
    # PostgreSQL cast shorthand silently leaves a literal ':' in the statement.
    query = _RULE_SELECT + """
        WHERE p.slug = :payer_slug
          AND r.cpt_code = :cpt_code
          AND (
                CAST(:plan_type AS text) IS NULL
                OR r.plan_type = :plan_type
                OR r.plan_type = 'any'
              )
        ORDER BY (r.plan_type = :plan_type) DESC NULLS LAST, r.last_verified_at DESC
        LIMIT 1
    """
    async with session_scope() as session:
        result = await session.execute(
            text(query),
            {"payer_slug": payer_slug, "cpt_code": cpt_code, "plan_type": plan_type},
        )
        row = result.mappings().first()

    return _to_rule_result(row) if row else None


async def list_rules(payer_slug: str, limit: int = 200) -> list[PayerRuleResult]:
    query = _RULE_SELECT + """
        WHERE p.slug = :payer_slug
        ORDER BY r.cpt_code
        LIMIT :limit
    """
    async with session_scope() as session:
        result = await session.execute(
            text(query), {"payer_slug": payer_slug, "limit": limit}
        )
        rows = result.mappings().all()
    return [_to_rule_result(row) for row in rows]


async def count_rules() -> int:
    async with session_scope() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM payer_rules"))
        return int(result.scalar_one())


async def list_payers() -> list[PayerSummary]:
    query = """
    SELECT
        p.slug,
        p.display_name,
        p.portal_base_url,
        COUNT(r.id)                              AS rule_count,
        MAX(c.finished_at)                       AS last_crawled_at,
        (ARRAY_AGG(c.status ORDER BY c.started_at DESC))[1] AS last_crawl_status
    FROM payers p
    LEFT JOIN payer_rules r      ON r.payer_id = p.id
    LEFT JOIN payer_crawl_runs c ON c.payer_id = p.id
    GROUP BY p.slug, p.display_name, p.portal_base_url
    ORDER BY p.display_name
    """
    async with session_scope() as session:
        rows = (await session.execute(text(query))).mappings().all()

    return [
        PayerSummary(
            slug=row["slug"],
            display_name=row["display_name"],
            portal_base_url=row["portal_base_url"],
            rule_count=row["rule_count"] or 0,
            last_crawled_at=row["last_crawled_at"],
            last_crawl_status=row["last_crawl_status"],
        )
        for row in rows
    ]


def _to_rule_result(row: Any) -> PayerRuleResult:
    last_verified = row["last_verified_at"]
    if last_verified.tzinfo is None:
        last_verified = last_verified.replace(tzinfo=UTC)
    staleness = (datetime.now(UTC) - last_verified).total_seconds() / 3600

    return PayerRuleResult(
        payer_slug=row["payer_slug"],
        payer_name=row["payer_name"],
        cpt_code=row["cpt_code"],
        icd10_codes=list(row["icd10_codes"] or []),
        plan_type=PlanType(row["plan_type"] or "any"),
        requires_pa=row["requires_pa"],
        criteria_text=row["criteria_text"],
        required_duration_weeks=row["required_duration_weeks"],
        required_conservative_care=list(row["required_conservative_care"] or []),
        required_imaging=list(row["required_imaging"] or []),
        source_url=row["source_url"],
        effective_date=row["effective_date"],
        last_verified_at=last_verified,
        staleness_hours=round(staleness, 2),
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

_UPSERT_RULE = """
INSERT INTO payer_rules (
    payer_id, cpt_code, icd10_codes, plan_type, requires_pa, criteria_text,
    required_duration_weeks, required_conservative_care, required_imaging,
    source_url, source_checksum, effective_date, last_verified_at
)
SELECT
    p.id, :cpt_code, :icd10_codes, :plan_type, :requires_pa, :criteria_text,
    :required_duration_weeks, :required_conservative_care, :required_imaging,
    :source_url, :checksum, :effective_date, now()
FROM payers p WHERE p.slug = :payer_slug
ON CONFLICT (payer_id, cpt_code, plan_type) DO UPDATE SET
    icd10_codes                = EXCLUDED.icd10_codes,
    requires_pa                = EXCLUDED.requires_pa,
    criteria_text              = EXCLUDED.criteria_text,
    required_duration_weeks    = EXCLUDED.required_duration_weeks,
    required_conservative_care = EXCLUDED.required_conservative_care,
    required_imaging           = EXCLUDED.required_imaging,
    source_url                 = EXCLUDED.source_url,
    source_checksum            = EXCLUDED.source_checksum,
    effective_date             = EXCLUDED.effective_date,
    last_verified_at           = now()
RETURNING id, (xmax = 0) AS inserted
"""


async def get_checksum(payer_slug: str, cpt_code: str, plan_type: str) -> str | None:
    query = """
    SELECT r.source_checksum
    FROM payer_rules r JOIN payers p ON p.id = r.payer_id
    WHERE p.slug = :payer_slug AND r.cpt_code = :cpt_code AND r.plan_type = :plan_type
    """
    async with session_scope() as session:
        result = await session.execute(
            text(query),
            {"payer_slug": payer_slug, "cpt_code": cpt_code, "plan_type": plan_type},
        )
        row = result.first()
    return row[0] if row else None


async def touch_rule(payer_slug: str, cpt_code: str, plan_type: str) -> None:
    """Record that an unchanged rule was re-verified.

    Freshness is a product feature — the UI shows how recently a rule was
    confirmed — so an unchanged crawl still has to move `last_verified_at`.
    """
    query = """
    UPDATE payer_rules r SET last_verified_at = now()
    FROM payers p
    WHERE p.id = r.payer_id
      AND p.slug = :payer_slug AND r.cpt_code = :cpt_code AND r.plan_type = :plan_type
    """
    async with session_scope() as session:
        await session.execute(
            text(query),
            {"payer_slug": payer_slug, "cpt_code": cpt_code, "plan_type": plan_type},
        )


async def upsert_rule(draft: RuleDraft, checksum: str) -> bool:
    """Write a rule. Returns True when a new row was inserted."""
    async with session_scope() as session:
        result = await session.execute(
            text(_UPSERT_RULE),
            {
                "payer_slug": draft.payer_slug,
                "cpt_code": draft.cpt_code,
                "icd10_codes": draft.icd10_codes,
                "plan_type": draft.plan_type.value,
                "requires_pa": draft.requires_pa,
                "criteria_text": draft.criteria_text,
                "required_duration_weeks": draft.required_duration_weeks,
                "required_conservative_care": draft.required_conservative_care,
                "required_imaging": draft.required_imaging,
                "source_url": draft.source_url,
                "checksum": checksum,
                "effective_date": draft.effective_date,
            },
        )
        row = result.first()
    return bool(row and row[1])


async def record_revision(payer_slug: str, cpt_code: str, plan_type: str, diff: dict) -> None:
    """Append to the immutable revision log."""
    query = """
    INSERT INTO payer_rule_revisions (rule_id, diff)
    SELECT r.id, CAST(:diff AS jsonb)
    FROM payer_rules r JOIN payers p ON p.id = r.payer_id
    WHERE p.slug = :payer_slug AND r.cpt_code = :cpt_code AND r.plan_type = :plan_type
    """
    import json

    async with session_scope() as session:
        await session.execute(
            text(query),
            {
                "payer_slug": payer_slug,
                "cpt_code": cpt_code,
                "plan_type": plan_type,
                "diff": json.dumps(diff, default=str),
            },
        )


async def record_crawl(result: CrawlResult) -> None:
    query = """
    INSERT INTO payer_crawl_runs
        (payer_id, status, rules_seen, rules_changed, error, started_at, finished_at)
    SELECT p.id, :status, :rules_seen, :rules_changed, :error, :started_at, :finished_at
    FROM payers p WHERE p.slug = :payer_slug
    """
    async with session_scope() as session:
        await session.execute(
            text(query),
            {
                "payer_slug": result.payer_slug,
                "status": result.status.value,
                "rules_seen": result.rules_seen,
                "rules_changed": result.rules_changed,
                "error": result.error,
                "started_at": result.started_at,
                "finished_at": result.finished_at,
            },
        )

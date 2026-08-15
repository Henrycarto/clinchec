"""Diff freshly crawled drafts against stored rules and persist the changes.

The important property here is *quietness*. A payer republishing the same
criteria must produce zero writes and zero alerts, because a rules feed that
cries wolf nightly gets ignored — and the one night the criteria genuinely
change is the night that matters. Change detection is therefore checksum-based
over the adjudicating fields only.
"""

from __future__ import annotations

import asyncio
import logging

from celery import shared_task

from app.config import get_settings
from app.db import (
    count_absences,
    dispose_engine,
    get_checksum,
    get_rule,
    init_engine,
    record_revision,
    replace_clauses,
    touch_rule,
    upsert_rule,
)
from app.schemas import CrawlResult, RuleDraft, SyncReport

logger = logging.getLogger(__name__)

# Fields whose change is worth telling a practice about.
_MATERIAL_FIELDS = (
    "requires_pa",
    "required_duration_weeks",
    "required_conservative_care",
    "required_imaging",
    "icd10_codes",
)

#: Clause changes are reported separately — a new exclusion is the single most
#: consequential thing a payer can publish, and it must never be buried in a
#: list diff alongside a reworded criteria paragraph.
_CLAUSE_FIELD = "clauses"


async def apply_drafts(
    payer_slug: str,
    drafts: list[RuleDraft],
    crawl: CrawlResult | None = None,
) -> SyncReport:
    """Persist a crawl's drafts, writing only what actually changed.

    `crawl` carries whether the run actually surveyed the payer. Without it,
    absence is not counted at all — the safe default for callers that only mean
    to write drafts, such as a manual re-sync.
    """
    report = SyncReport(payer_slug=payer_slug)

    for draft in drafts:
        checksum = draft.checksum()
        plan_type = draft.plan_type.value

        stored_checksum = await get_checksum(payer_slug, draft.cpt_code, plan_type)

        if stored_checksum == checksum:
            # Same criteria — just record that we re-verified them today.
            await touch_rule(payer_slug, draft.cpt_code, plan_type)
            report.unchanged += 1
            continue

        previous = None
        if stored_checksum is not None:
            previous = await get_rule(payer_slug, draft.cpt_code, plan_type)

        inserted = await upsert_rule(draft, checksum)

        # Clauses are replaced wholesale, so a dropped exclusion widens coverage
        # instead of lingering and denying requests the payer now approves.
        await replace_clauses(payer_slug, draft.cpt_code, plan_type, draft.clauses)

        if inserted:
            report.created += 1
            logger.info(
                "%s/%s: new rule (%d clause(s))",
                payer_slug, draft.cpt_code, len(draft.clauses),
            )
            continue

        report.updated += 1
        diff = _diff(previous, draft)
        if diff:
            await record_revision(payer_slug, draft.cpt_code, plan_type, diff)
            logger.warning(
                "%s/%s: criteria changed — %s",
                payer_slug,
                draft.cpt_code,
                ", ".join(diff),
            )
            report.changes.append({"cpt_code": draft.cpt_code, "fields": diff})

    await _retire_absent(payer_slug, drafts, crawl, report)
    return report


async def _retire_absent(
    payer_slug: str,
    drafts: list[RuleDraft],
    crawl: CrawlResult | None,
    report: SyncReport,
) -> None:
    """Withdraw rules whose source document has stopped being published.

    The whole difficulty is telling that from a crawl that merely came back
    short. A payer being down, discovery returning stale paths, the page cap
    truncating the document list, an offline seed run — all produce fewer rules
    and none of them says a policy was withdrawn. The August 2026 validation is
    the case to keep in mind: UHC discovery returned four stale paths and parsed
    nothing, which "absent means gone" would have read as UnitedHealthcare
    withdrawing its entire rule set.

    So absence is only counted when the crawl reports itself complete, and
    retirement waits for several complete crawls in a row. Producing a rule at
    any point resets the count and reverses a retirement.
    """
    settings = get_settings()
    if crawl is None or not crawl.complete:
        reason = (
            "no crawl result supplied"
            if crawl is None
            else crawl.incomplete_reason or "crawl reported incomplete"
        )
        logger.info(
            "%s: not counting absences (%s); stored rules are left alone",
            payer_slug,
            reason,
        )
        return

    seen = [(d.cpt_code, d.plan_type.value) for d in drafts]
    retired, counting = await count_absences(
        payer_slug, seen, settings.rule_retire_after_missed_crawls
    )

    for cpt_code, plan_type in counting:
        logger.info(
            "%s/%s (%s): not produced by a complete crawl; counting toward "
            "retirement after %d",
            payer_slug,
            cpt_code,
            plan_type,
            settings.rule_retire_after_missed_crawls,
        )

    for cpt_code, plan_type in retired:
        # Warning, not info: this withdraws criteria from every practice using
        # them, and it is the one outcome here that changes what Scan returns.
        logger.warning(
            "%s/%s (%s): retired — absent from %d consecutive complete crawls",
            payer_slug,
            cpt_code,
            plan_type,
            settings.rule_retire_after_missed_crawls,
        )

    report.retired = len(retired)
    report.retiring = len(counting)


def _diff(previous, draft: RuleDraft) -> dict:  # noqa: ANN001
    """Field-level diff restricted to criteria that change adjudication."""
    if previous is None:
        return {}

    changes: dict[str, dict] = {}
    for field in _MATERIAL_FIELDS:
        before = getattr(previous, field, None)
        after = getattr(draft, field, None)
        if isinstance(before, list) and isinstance(after, list):
            if sorted(before) != sorted(after):
                changes[field] = {"previous": before, "current": after}
        elif before != after:
            changes[field] = {"previous": before, "current": after}

    clause_diff = _diff_clauses(
        getattr(previous, "clauses", []) or [], draft.clauses
    )
    if clause_diff:
        changes[_CLAUSE_FIELD] = clause_diff

    return changes


def _diff_clauses(previous: list, current: list) -> dict:  # noqa: ANN001
    """Report clauses added, removed, or flipped in polarity.

    Reported separately from the flat criteria fields because the consequences
    differ in kind. A new *exclusion* means requests Clinchec approved yesterday
    will be denied today — the practice needs to know before submitting, not
    after a denial. A removed exclusion widens coverage and is worth surfacing
    for the opposite reason.
    """
    def key(clause) -> tuple[str, str]:  # noqa: ANN001
        polarity = getattr(clause.polarity, "value", clause.polarity)
        return (polarity, " ".join(clause.indication_text.split()))

    before = {key(c): c for c in previous}
    after = {key(c): c for c in current}

    added = [k for k in after if k not in before]
    removed = [k for k in before if k not in after]
    if not added and not removed:
        return {}

    return {
        "added": [{"polarity": p, "indication": i} for p, i in added],
        "removed": [{"polarity": p, "indication": i} for p, i in removed],
        # Surfaced explicitly so an alerting rule can key on it directly.
        "new_exclusions": [i for p, i in added if p == "excluded"],
        "lifted_exclusions": [i for p, i in removed if p == "excluded"],
    }


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@shared_task(name="app.tasks.rules_sync.audit_freshness")
def audit_freshness(max_age_days: int = 30) -> dict:
    """Flag rules nobody has re-verified recently.

    A silently stale rule is worse than a missing one: the UI presents it with
    the same confidence as a rule confirmed this morning. This surfaces the gap
    so it can be alerted on.
    """
    return asyncio.run(_audit_freshness(max_age_days))


async def _audit_freshness(max_age_days: int) -> dict:
    from sqlalchemy import text

    from app.db import session_scope

    settings = get_settings()
    init_engine(settings)

    query = """
    SELECT p.slug, r.cpt_code,
           EXTRACT(EPOCH FROM (now() - r.last_verified_at)) / 86400 AS age_days
    FROM payer_rules r JOIN payers p ON p.id = r.payer_id
    WHERE r.last_verified_at < now() - make_interval(days => :max_age_days)
    ORDER BY age_days DESC
    """
    try:
        async with session_scope() as session:
            rows = (
                await session.execute(text(query), {"max_age_days": max_age_days})
            ).mappings().all()
    finally:
        await dispose_engine()

    stale = [
        {"payer_slug": row["slug"], "cpt_code": row["cpt_code"],
         "age_days": round(float(row["age_days"]), 1)}
        for row in rows
    ]

    if stale:
        logger.warning("%d payer rules are older than %d days", len(stale), max_age_days)

    return {"stale_count": len(stale), "max_age_days": max_age_days, "stale": stale[:50]}


@shared_task(name="app.tasks.rules_sync.seed_from_adapters")
def seed_from_adapters() -> dict:
    """Load every adapter's published-criteria seed set into the database.

    Run once on a fresh deployment so the rules table is useful before the
    first live crawl completes.
    """
    return asyncio.run(_seed_from_adapters())


async def _seed_from_adapters() -> dict:
    from app.payers import build_adapters

    settings = get_settings()
    init_engine(settings)

    totals = {"created": 0, "updated": 0, "unchanged": 0}
    try:
        for adapter in build_adapters(settings):
            report = await apply_drafts(adapter.slug, adapter.seed_rules())
            totals["created"] += report.created
            totals["updated"] += report.updated
            totals["unchanged"] += report.unchanged
    finally:
        await dispose_engine()

    logger.info("Seeded payer rules: %s", totals)
    return totals

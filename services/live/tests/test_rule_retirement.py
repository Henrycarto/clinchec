"""Retiring rules whose source document stopped being published.

The interesting tests here are the ones asserting that retirement does *not*
happen. A crawl produces fewer rules for many reasons that say nothing about
what a payer publishes — it was down, discovery returned stale paths, the page
cap truncated the document list, the run served offline seeds — and the August
2026 validation is the case to keep in mind: UnitedHealthcare discovery returned
four stale paths and parsed nothing. "Absent means gone" would have read that as
UnitedHealthcare withdrawing its entire rule set.

So the sweep runs only on a crawl that reports itself complete, and the
completeness flag defaults to False. These tests pin both halves.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.schemas import CrawlResult, CrawlStatus, PlanType, RuleDraft, SyncReport
from app.tasks import rules_sync


def _crawl(**kwargs) -> CrawlResult:
    base = {
        "payer_slug": "uhc",
        "status": CrawlStatus.SUCCEEDED,
        "started_at": datetime.now(UTC),
        "complete": True,
    }
    return CrawlResult(**(base | kwargs))


def _draft(cpt_code: str = "27447") -> RuleDraft:
    return RuleDraft(
        payer_slug="uhc",
        cpt_code=cpt_code,
        plan_type=PlanType.COMMERCIAL,
        criteria_text="x" * 200,
    )


@pytest.fixture
def swept(monkeypatch):
    """Capture what the sweep would ask the database to do."""
    calls: list[dict] = []

    async def fake_count_absences(payer_slug, seen, retire_after):
        calls.append(
            {"payer_slug": payer_slug, "seen": seen, "retire_after": retire_after}
        )
        return [("72148", "commercial")], [("73721", "commercial")]

    monkeypatch.setattr(rules_sync, "count_absences", fake_count_absences)
    return calls


async def _sweep(drafts, crawl) -> tuple[SyncReport, list]:
    report = SyncReport(payer_slug="uhc")
    await rules_sync._retire_absent("uhc", drafts, crawl, report)
    return report


# --- when it must not fire -------------------------------------------------


@pytest.mark.asyncio
async def test_no_sweep_without_a_crawl_result(swept):
    """A manual re-sync writes drafts; it does not survey the payer."""
    await _sweep([_draft()], None)
    assert swept == []


@pytest.mark.asyncio
async def test_no_sweep_when_the_page_cap_truncated_the_run(swept):
    """Rules sourced from documents 41-49 are absent because we stopped
    reading, not because the payer stopped publishing."""
    await _sweep(
        [_draft()],
        _crawl(complete=False, incomplete_reason="capped at 40 of 49 documents"),
    )
    assert swept == []


@pytest.mark.asyncio
async def test_no_sweep_when_a_document_could_not_be_fetched(swept):
    await _sweep(
        [_draft()],
        _crawl(complete=False, incomplete_reason="1 document(s) could not be fetched"),
    )
    assert swept == []


@pytest.mark.asyncio
async def test_no_sweep_in_offline_seed_mode(swept):
    """Seeds are a deliberate subset. Everything they omit, we omitted."""
    await _sweep(
        [_draft()],
        _crawl(
            status=CrawlStatus.SKIPPED,
            complete=False,
            incomplete_reason="offline seed mode",
        ),
    )
    assert swept == []


@pytest.mark.asyncio
async def test_completeness_defaults_to_false():
    """A code path that forgets to set it withholds the conclusion.

    The failure this prevents is silent: a new adapter, or a new branch in an
    old one, returning a CrawlResult that happens to look complete and quietly
    retiring rules it never looked for.
    """
    assert CrawlResult(
        payer_slug="uhc", status=CrawlStatus.SUCCEEDED, started_at=datetime.now(UTC)
    ).complete is False


# --- when it must fire -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_complete_crawl_counts_absences(swept):
    report = await _sweep([_draft("27447")], _crawl())

    assert len(swept) == 1
    assert swept[0]["seen"] == [("27447", "commercial")]
    assert swept[0]["retire_after"] == 3
    assert report.retired == 1
    assert report.retiring == 1


@pytest.mark.asyncio
async def test_a_complete_crawl_with_no_drafts_still_sweeps(swept):
    """A payer can withdraw every policy we track.

    Guarding the sweep behind `if drafts:` would make that the one case
    retirement never reaches — the rules would sit there forever, citing
    documents that no longer exist.
    """
    report = await _sweep([], _crawl())
    assert len(swept) == 1
    assert swept[0]["seen"] == []
    assert report.retired == 1

"""Adapter tests — criteria parsing, change detection and politeness.

No network: the crawl mechanics are exercised against a mock transport, and the
parsing is exercised against representative policy prose.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.payers import REGISTERED_SLUGS, build_adapter, build_adapters
from app.payers.base import PayerAdapter
from app.schemas import CrawlStatus, PlanType, RuleDraft

AETNA_POLICY_PROSE = """
Policy
Aetna considers magnetic resonance imaging (MRI) of the lumbar spine medically
necessary for members with low back pain that has persisted for at least 6 weeks
despite a documented trial of conservative therapy, including physical therapy
and NSAIDs. Plain radiographs should be obtained prior to advanced imaging.
Applicable codes: CPT code 72148. Diagnosis codes M54.16, M51.26, M48.06.
Background
The literature on lumbar imaging is extensive and has been reviewed elsewhere.
References
1. Chou R, et al. Ann Intern Med. 2011.
"""

NO_PA_PROSE = """
Home sleep apnea testing does not require prior authorization for members with a
high pre-test probability of obstructive sleep apnea. CPT code 95806.
"""


@pytest.fixture
def settings() -> Settings:
    return Settings(
        offline_seed_mode=True,
        respect_robots_txt=False,
        payer_request_delay_seconds=0.0,
        database_url="postgresql+asyncpg://x:x@localhost/x",
    )


# --- Registry ---------------------------------------------------------------

def test_all_three_launch_payers_are_registered():
    assert set(REGISTERED_SLUGS) == {"aetna", "bcbs", "uhc"}


def test_adapters_declare_slug_and_name(settings):
    for adapter in build_adapters(settings):
        assert adapter.slug
        assert adapter.display_name
        assert isinstance(adapter, PayerAdapter)


def test_unknown_payer_returns_none(settings):
    assert build_adapter("cigna", settings) is None


# --- Criteria parsing -------------------------------------------------------

def test_extracts_duration_requirement():
    assert PayerAdapter.extract_duration_weeks(AETNA_POLICY_PROSE) == 6


def test_duration_takes_the_longest_stated_minimum():
    text = "at least 6 weeks of therapy, and a minimum of 6 months of documented care"
    assert PayerAdapter.extract_duration_weeks(text) == 26


def test_no_duration_returns_none():
    assert PayerAdapter.extract_duration_weeks("No waiting period applies.") is None


def test_extracts_conservative_care_requirements():
    care = PayerAdapter.extract_conservative_care(AETNA_POLICY_PROSE)
    assert "physical therapy" in care
    assert "NSAIDs" in care


def test_extracts_imaging_requirements():
    assert "radiographs" in PayerAdapter.extract_imaging(AETNA_POLICY_PROSE)


def test_extracts_icd10_codes():
    codes = PayerAdapter.extract_icd10(AETNA_POLICY_PROSE)
    assert "M54.16" in codes
    assert "M51.26" in codes


def test_cpt_extraction_requires_a_nearby_cue():
    # A bare five-digit run (a ZIP code, a year range) must not become a CPT.
    assert PayerAdapter.extract_cpt_codes("Serving members in 60614 since 1982.") == []
    assert "72148" in PayerAdapter.extract_cpt_codes(AETNA_POLICY_PROSE)


def test_detects_when_prior_authorization_is_not_required():
    draft = PayerAdapter.parse_criteria(
        NO_PA_PROSE, payer_slug="aetna", cpt_code="95806"
    )
    assert draft.requires_pa is False


def test_parse_criteria_produces_a_complete_draft():
    draft = PayerAdapter.parse_criteria(
        AETNA_POLICY_PROSE,
        payer_slug="aetna",
        cpt_code="72148",
        source_url="https://example.test/cpb/0743.html",
        plan_type=PlanType.COMMERCIAL,
    )
    assert draft.cpt_code == "72148"
    assert draft.requires_pa is True
    assert draft.required_duration_weeks == 6
    assert draft.plan_type is PlanType.COMMERCIAL
    assert draft.source_url.endswith("0743.html")


# --- Change detection -------------------------------------------------------

def _draft(**overrides) -> RuleDraft:
    base = {
        "payer_slug": "aetna",
        "cpt_code": "72148",
        "icd10_codes": ["M54.16", "M51.26"],
        "requires_pa": True,
        "criteria_text": "At least 6 weeks of conservative therapy.",
        "required_duration_weeks": 6,
        "required_conservative_care": ["physical therapy"],
        "required_imaging": [],
    }
    return RuleDraft(**{**base, **overrides})


def test_identical_criteria_produce_the_same_checksum():
    assert _draft().checksum() == _draft().checksum()


def test_icd_code_order_does_not_change_the_checksum():
    assert _draft().checksum() == _draft(icd10_codes=["M51.26", "M54.16"]).checksum()


def test_whitespace_reflow_does_not_change_the_checksum():
    reflowed = _draft(criteria_text="At least 6 weeks   of\nconservative therapy.")
    assert _draft().checksum() == reflowed.checksum()


def test_a_new_source_url_is_not_a_rule_change():
    # Payers move documents constantly; that must not alert every practice.
    moved = _draft(source_url="https://example.test/moved.html")
    assert _draft().checksum() == moved.checksum()


def test_a_changed_duration_is_a_rule_change():
    assert _draft().checksum() != _draft(required_duration_weeks=12).checksum()


def test_a_changed_pa_requirement_is_a_rule_change():
    assert _draft().checksum() != _draft(requires_pa=False).checksum()


# --- Seed rules -------------------------------------------------------------

def test_every_adapter_ships_usable_seed_rules(settings):
    for adapter in build_adapters(settings):
        seeds = adapter.seed_rules()
        assert seeds, f"{adapter.slug} has no seed rules"
        for seed in seeds:
            assert seed.payer_slug == adapter.slug
            assert seed.cpt_code
            assert len(seed.criteria_text) > 40


def test_seed_rules_have_stable_checksums(settings):
    adapter = build_adapter("aetna", settings)
    first = [d.checksum() for d in adapter.seed_rules()]
    second = [d.checksum() for d in adapter.seed_rules()]
    assert first == second


# --- Crawl mechanics --------------------------------------------------------

async def test_offline_seed_mode_skips_the_network(settings):
    adapter = build_adapter("aetna", settings)

    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"offline mode must not fetch {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(explode)) as client:
        result, drafts = await adapter.sync(client)

    assert result.status is CrawlStatus.SKIPPED
    assert result.pages_fetched == 0
    assert drafts


async def test_a_failing_payer_does_not_raise(settings):
    live_settings = settings.model_copy(update={"offline_seed_mode": False})
    adapter = build_adapter("aetna", live_settings)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("portal unreachable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as client:
        result, drafts = await adapter.safe_sync(client)

    assert result.status is CrawlStatus.FAILED
    assert result.error
    assert drafts == []


async def test_unreachable_policy_page_is_skipped_not_fatal(settings):
    live_settings = settings.model_copy(update={"offline_seed_mode": False})
    adapter = build_adapter("aetna", live_settings)

    index = "".join(
        f'<a href="/cpb/medical/data/700_799/{n}.html">CPB</a>' for n in ("0743", "0660")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("cpb_alpha.html"):
            return httpx.Response(200, text=index)
        if "0743" in request.url.path:
            return httpx.Response(200, text=f"<html><body>{AETNA_POLICY_PROSE}</body></html>")
        return httpx.Response(503, text="unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result, drafts = await adapter.safe_sync(client)

    assert result.status is CrawlStatus.SUCCEEDED
    # 0743 parsed; 0660 returned 503 and was skipped rather than failing the run.
    assert [d.cpt_code for d in drafts] == ["72148"]


async def test_parsed_rule_matches_the_policy_prose(settings):
    live_settings = settings.model_copy(update={"offline_seed_mode": False})
    adapter = build_adapter("aetna", live_settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("cpb_alpha.html"):
            return httpx.Response(
                200, text='<a href="/cpb/medical/data/700_799/0743.html">CPB 0743</a>'
            )
        return httpx.Response(200, text=f"<html><body>{AETNA_POLICY_PROSE}</body></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        _, drafts = await adapter.sync(client)

    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.cpt_code == "72148"
    assert draft.required_duration_weeks == 6
    assert "physical therapy" in draft.required_conservative_care
    # The Background and References sections must not leak into criteria.
    assert "Chou R" not in draft.criteria_text


async def test_mangled_markup_yields_no_rules_rather_than_junk(settings):
    """A payer redesign must not silently overwrite good criteria with noise."""
    live_settings = settings.model_copy(update={"offline_seed_mode": False})
    adapter = build_adapter("aetna", live_settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("cpb_alpha.html"):
            return httpx.Response(
                200, text='<a href="/cpb/medical/data/700_799/0743.html">CPB</a>'
            )
        return httpx.Response(200, text="<html><body><div>Loading…</div></body></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        _, drafts = await adapter.sync(client)

    assert drafts == []

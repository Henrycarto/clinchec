"""Offline coverage for the criteria content guard.

`guard.assess()` was calibrated against 8 live Aetna CPB pages and validated by
hand at 27/27. That validation needed a fetched corpus, so it could not run in
CI — which left the module that decides whether crawled criteria are real with
no automated coverage at all.

These tests replay the same three regions per bulletin from a committed
fixture, so the guard is exercised on every push with no network access and no
load on Aetna's servers.

    policy      the real Policy section          -> must be ACCEPTED
    chrome      what the superseded flattened    -> must be REJECTED
                regex extracted: page navigation
    background  the Background section           -> must be REJECTED
                (right page, wrong region)

The Background case is the one that matters. A naive keyword check for
"medically necessary" passes it, because literature review says that constantly
while summarising trials — which is why the guard scores marker *density* and
region *structure* rather than presence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.payers as payers_pkg
from app.payers.guard import (
    MAX_CHROME_DENSITY,
    MIN_NECESSITY_DENSITY,
    assess,
    should_overwrite_stored_rule,
    strip_sentinels,
)

FIXTURE = Path(payers_pkg.__file__).parent / "fixtures" / "aetna_cpb_regions.json"


def _load() -> dict:
    if not FIXTURE.exists():  # pragma: no cover - core not mounted
        pytest.skip(f"fixture not present at {FIXTURE}")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


FIXTURES = _load()
PAGES = FIXTURES["pages"]
PAGE_IDS = sorted(PAGES)


def _stub(sample_len: int, region_chars: int, page_chars: int) -> str:
    """Page stand-in preserving the real extraction ratio.

    `assess` reads `page_text` only for its length. Background samples are
    size-capped in the fixture, so the stub is scaled to keep the proportion
    the full document had — otherwise truncation alone would drop a 70%
    extraction under the ratio ceiling and flip the verdict.
    """
    if not region_chars:
        return "x" * page_chars
    return "x" * max(1, round(sample_len * page_chars / region_chars))


# --- the three regions ------------------------------------------------------

@pytest.mark.parametrize("cpb", PAGE_IDS)
def test_policy_section_is_accepted(cpb: str) -> None:
    page = PAGES[cpb]
    text = strip_sentinels(page["policy_marked"])
    result = assess(text, "x" * page["page_chars"])
    assert result.accepted, f"CPB {cpb} ({page['title']}) rejected: {result.reason}"


@pytest.mark.parametrize("cpb", PAGE_IDS)
def test_navigation_chrome_is_rejected(cpb: str) -> None:
    """The original failure: 212 chars of page furniture read as criteria."""
    page = PAGES[cpb]
    result = assess(page["chrome"], "x" * page["page_chars"], heading_found=bool(page["chrome"]))
    assert not result.accepted, f"CPB {cpb} chrome accepted"
    assert "chrome_dominant" in result.rejections


@pytest.mark.parametrize("cpb", PAGE_IDS)
def test_background_section_is_rejected(cpb: str) -> None:
    """Right page, wrong region — the case a keyword check would pass."""
    page = PAGES[cpb]
    sample = page["background_sample"]
    stub = _stub(len(sample), page["background_chars"], page["page_chars"])
    result = assess(sample, stub)
    assert not result.accepted, f"CPB {cpb} Background accepted: {result.reason}"


# --- the signals themselves -------------------------------------------------

def test_chrome_and_policy_separate_by_a_wide_margin() -> None:
    """The calibrated separation should hold, not just the verdicts."""
    chrome_floor = min(
        assess(p["chrome"], "x" * p["page_chars"], heading_found=True).chrome_density
        for p in PAGES.values() if p["chrome"]
    )
    policy_ceiling = max(
        assess(strip_sentinels(p["policy_marked"]), "x" * p["page_chars"]).chrome_density
        for p in PAGES.values()
    )
    assert policy_ceiling < MAX_CHROME_DENSITY < chrome_floor
    assert chrome_floor / MAX_CHROME_DENSITY > 3, "junk margin has eroded"
    assert MAX_CHROME_DENSITY / max(policy_ceiling, 0.01) > 3, "valid margin has eroded"


def test_background_necessity_stays_below_policy() -> None:
    """Necessity density is what separates the two regions of the same page."""
    for cpb, page in PAGES.items():
        policy = assess(strip_sentinels(page["policy_marked"]), "x" * page["page_chars"])
        sample = page["background_sample"]
        background = assess(
            sample,
            _stub(len(sample), page["background_chars"], page["page_chars"]),
        )
        assert background.necessity_density < MIN_NECESSITY_DENSITY <= policy.necessity_density, (
            f"CPB {cpb}: background {background.necessity_density} vs "
            f"policy {policy.necessity_density}"
        )


# --- degradation behaviour --------------------------------------------------

def test_empty_text_is_rejected() -> None:
    assert assess("", "x" * 1000).rejections == ("empty",)


def test_missing_heading_is_a_hard_reject_regardless_of_content() -> None:
    """A payer redesign must not be rescued by text that happens to read well."""
    page = PAGES[PAGE_IDS[0]]
    good = strip_sentinels(page["policy_marked"])
    result = assess(good, "x" * page["page_chars"], heading_found=False)
    assert not result.accepted
    assert "no_policy_heading" in result.rejections


def test_rejected_parses_never_overwrite_a_stored_rule() -> None:
    """Stale-and-flagged beats confidently wrong."""
    page = PAGES[PAGE_IDS[0]]
    junk = assess(page["chrome"], "x" * page["page_chars"], heading_found=True)
    good = assess(strip_sentinels(page["policy_marked"]), "x" * page["page_chars"])
    assert should_overwrite_stored_rule(good) is True
    assert should_overwrite_stored_rule(junk) is False


def test_length_alone_would_not_have_separated_these() -> None:
    """Guards the reasoning, not just the code.

    The superseded check was `len(text) < 80`. Policy sections run as short as
    ~600 chars while chrome reaches ~220 — overlapping ranges with a real
    policy inside the gap, so no length threshold works. If a future change
    reintroduces one, this fails.
    """
    policy_floor = min(len(strip_sentinels(p["policy_marked"])) for p in PAGES.values())
    chrome_ceiling = max(len(p["chrome"]) for p in PAGES.values())
    assert policy_floor / chrome_ceiling < 5, (
        "policy and chrome lengths are close enough that length is not a usable "
        "signal; do not reintroduce a length threshold"
    )

"""Indication-scoped adjudication.

Crawl validation found that 4 of 10 verified payer/procedure pairs are MIXED —
the same CPT is approved for one indication and explicitly excluded for another.
Aetna CPB 0673 approves partial meniscectomy for mechanical symptoms with mild
osteoarthritis, and calls it experimental for meniscal root tears.

A single `requires_pa` boolean cannot express that. Scoring a root-tear request
against mechanical-symptom criteria returns a confident green on a request the
bulletin explicitly denies — the same failure shape as mapping lumbar MRI to the
spinal-fusion bulletin, one layer up.

These tests pin the behaviour that fixes it:

  * an exclusion short-circuits, and returns no improvable drivers
  * exclusions win over coverage clauses that also match
  * a clause's criteria override the payer-level defaults
  * "this payer addresses nothing about this indication" is distinguishable
    from "this payer has no criteria at all", rather than both silently
    becoming the national baseline
"""

from __future__ import annotations

import pytest

from app.models.icd_extractor import extract_codes
from app.models.scoring import PayerRule, RuleClause, evaluate
from app.models.soap_parser import get_parser
from app.schemas import ApprovalBand, CoverageStatus

NOTE = """\
S: 62 y/o female with right knee pain for 14 months. Completed 12 weeks of
physical therapy, a corticosteroid injection, and activity modification without
relief. Unable to climb stairs.

O: Weight-bearing radiographs show medial compartment narrowing.

A: Right knee osteoarthritis.

P: Proceed with total knee arthroplasty, right.
"""

COVERED = RuleClause(
    polarity="covered",
    indication_text="advanced osteoarthritis with functionally limiting pain",
    indication_icd10_prefixes=("M17",),
    required_duration_weeks=12,
    required_conservative_care=("physical therapy",),
    required_imaging=("weight-bearing radiographs",),
    source_snippet="Total knee arthroplasty is considered medically necessary...",
)

EXCLUDED = RuleClause(
    polarity="excluded",
    indication_text="knee pain without radiographic degenerative change",
    indication_icd10_prefixes=("M25",),
    source_snippet="...is considered experimental, investigational, or unproven.",
)


@pytest.fixture(scope="module")
def scan():
    parser = get_parser("en_core_web_sm")
    parsed = parser.parse(NOTE)
    diagnoses, procedures = extract_codes(parsed.entities, NOTE)
    return parsed, diagnoses, procedures


def _rule(*clauses: RuleClause, **kwargs) -> PayerRule:
    return PayerRule(
        payer_slug="aetna", cpt_code="27447", clauses=tuple(clauses), **kwargs
    )


# --- exclusions -------------------------------------------------------------

def test_matching_exclusion_short_circuits_to_red(scan) -> None:
    parsed, diagnoses, procedures = scan
    # Force the exclusion to match this patient's documented diagnosis.
    exclusion = EXCLUDED.__class__(
        polarity="excluded",
        indication_text="osteoarthritis of the knee",
        indication_icd10_prefixes=("M17",),
        source_snippet="Aetna considers this experimental for this indication.",
    )
    result = evaluate(
        parsed, diagnoses, procedures[0], payer_rule=_rule(COVERED, exclusion)
    )

    assert result.band is ApprovalBand.RED
    assert result.coverage_status is CoverageStatus.EXCLUDED
    assert result.score < 0.05


def test_exclusion_offers_no_improvable_drivers(scan) -> None:
    """An exclusion is decided on indication alone.

    Presenting driver deltas would imply the request is improvable by better
    documentation. It is not — so the assessment must not offer a list of gaps
    to close.
    """
    parsed, diagnoses, procedures = scan
    exclusion = RuleClause(
        polarity="excluded",
        indication_text="osteoarthritis of the knee",
        indication_icd10_prefixes=("M17",),
    )
    result = evaluate(parsed, diagnoses, procedures[0], payer_rule=_rule(exclusion))

    assert result.missing_elements == []
    assert [d.key for d in result.drivers] == ["payer_exclusion"]


def test_exclusion_quotes_the_payer_verbatim(scan) -> None:
    """The payer's own sentence is the strongest thing to put in an appeal."""
    parsed, diagnoses, procedures = scan
    exclusion = RuleClause(
        polarity="excluded",
        indication_text="osteoarthritis of the knee",
        indication_icd10_prefixes=("M17",),
        source_snippet="Aetna considers the following experimental: ...",
    )
    result = evaluate(parsed, diagnoses, procedures[0], payer_rule=_rule(exclusion))

    assert result.payer_quote == "Aetna considers the following experimental: ..."
    assert result.matched_indication == "osteoarthritis of the knee"


def test_exclusion_wins_when_both_clauses_match(scan) -> None:
    """If a request matches coverage *and* an exclusion, the payer denies it."""
    parsed, diagnoses, procedures = scan
    both = RuleClause(
        polarity="excluded",
        indication_text="also matches M17",
        indication_icd10_prefixes=("M17",),
    )
    result = evaluate(parsed, diagnoses, procedures[0], payer_rule=_rule(COVERED, both))
    assert result.coverage_status is CoverageStatus.EXCLUDED


# --- coverage ---------------------------------------------------------------

def test_matching_coverage_clause_is_adjudicated(scan) -> None:
    parsed, diagnoses, procedures = scan
    result = evaluate(parsed, diagnoses, procedures[0], payer_rule=_rule(COVERED))

    assert result.coverage_status is CoverageStatus.ADJUDICATED
    assert result.band is ApprovalBand.GREEN
    assert result.matched_indication == COVERED.indication_text


def test_clause_criteria_override_payer_defaults(scan) -> None:
    """The clause decides the thresholds, not the payer-level fallback."""
    parsed, diagnoses, procedures = scan
    strict = RuleClause(
        polarity="covered",
        indication_text="osteoarthritis",
        indication_icd10_prefixes=("M17",),
        # 14 months documented, so a 24-month clause must fail the duration driver
        # even though the payer-level default of 12 weeks would pass.
        required_duration_weeks=104,
    )
    rule = _rule(strict, required_duration_weeks=12)
    result = evaluate(parsed, diagnoses, procedures[0], payer_rule=rule)

    duration = next(d for d in result.drivers if d.key == "duration")
    assert duration.satisfied is False


# --- the epistemic states ---------------------------------------------------

def test_unaddressed_indication_is_distinguishable(scan) -> None:
    """A payer with clauses that miss this indication is not a silent baseline."""
    parsed, diagnoses, procedures = scan
    elsewhere = RuleClause(
        polarity="covered",
        indication_text="rheumatoid arthritis",
        indication_icd10_prefixes=("M05",),
    )
    result = evaluate(parsed, diagnoses, procedures[0], payer_rule=_rule(elsewhere))

    assert result.coverage_status is CoverageStatus.INDICATION_NOT_ADDRESSED
    assert "none covering this indication" in result.rationale


def test_no_payer_rule_reports_no_criteria_available(scan) -> None:
    """The Anthem case: nothing synced must not masquerade as a payer decision."""
    parsed, diagnoses, procedures = scan
    result = evaluate(parsed, diagnoses, procedures[0], payer_rule=None)

    assert result.coverage_status is CoverageStatus.NO_CRITERIA_AVAILABLE
    assert result.basis == "rule_engine"


def test_clauseless_payer_rule_still_adjudicates(scan) -> None:
    """A bulletin may adjudicate a procedure unconditionally; that is legitimate."""
    parsed, diagnoses, procedures = scan
    rule = _rule(required_duration_weeks=12, requires_conservative_care=True)
    result = evaluate(parsed, diagnoses, procedures[0], payer_rule=rule)

    assert result.coverage_status is CoverageStatus.ADJUDICATED
    assert result.matched_indication is None


def test_clause_without_icd_prefixes_applies_unconditionally(scan) -> None:
    parsed, diagnoses, procedures = scan
    catch_all = RuleClause(
        polarity="covered",
        indication_text="all indications",
        required_duration_weeks=12,
    )
    result = evaluate(parsed, diagnoses, procedures[0], payer_rule=_rule(catch_all))
    assert result.coverage_status is CoverageStatus.ADJUDICATED

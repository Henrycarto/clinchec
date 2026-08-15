"""Procedures the payer adjudicates against criteria it does not publish.

UnitedHealthcare's commercial knee, hip and shoulder policies say the surgery
"is proven and medically necessary in certain circumstances" and then defer to
InterQual, which is licensed content. The circumstances — the part that decides
the request — are not in the document and are not anywhere we can read.

The tempting behaviour is the dangerous one. The deferral paragraph contains
"medically necessary", so a parser reads it as coverage, stores it as
`criteria_text`, and scoring returns a confidence number. That number is
indistinguishable from one computed against real criteria: same band, same
drivers, same source URL, same recent timestamp. Nobody downstream can tell that
the criteria were never seen.

So a delegated clause is carried through the whole pipeline as a distinct thing:
never selected, never scored, always surfaced, and reported as
`criteria_delegated` rather than `adjudicated`.
"""

from __future__ import annotations

import pytest

from app.routers.extract import _polarity
from app.schemas import CodeCandidate, CoverageStatus

try:  # pragma: no cover - depends on whether the private core is mounted
    from app.models.icd_extractor import extract_codes
    from app.models.scoring import PayerRule, RuleClause, evaluate
    from app.models.soap_parser import get_parser
except ImportError:  # pragma: no cover
    pytest.skip("clinchec-core-scan not mounted", allow_module_level=True)

NOTE = """\
S: 68 y/o male with left knee pain for 18 months. Completed 14 weeks of physical
therapy and NSAIDs without relief. Unable to walk more than one block.

O: Weight-bearing radiographs show tricompartmental joint space loss.

A: Left knee osteoarthritis.

P: Proceed with total knee arthroplasty, left.
"""

DELEGATED = RuleClause(
    polarity="delegated",
    indication_text=(
        "Medical-necessity review is delegated to InterQual. The payer does not "
        "publish the criteria, so this request cannot be pre-screened against "
        "them."
    ),
    source_snippet=(
        "Surgery of the knee is proven and medically necessary — For medical "
        "necessity clinical coverage criteria, refer to the InterQual CP: "
        "Procedures"
    ),
    advisory=True,
)

#: The same policy states its own exclusions. Those still deny — UHC wrote them.
EXCLUDED = RuleClause(
    polarity="excluded",
    indication_text="knee pain without radiographic degenerative change",
    indication_icd10_prefixes=("M25",),
    source_snippet="...is unproven and not medically necessary.",
)


@pytest.fixture(scope="module")
def scan():
    parser = get_parser("en_core_web_sm")
    parsed = parser.parse(NOTE)
    diagnoses, procedures = extract_codes(parsed.entities, NOTE)
    return parsed, diagnoses, procedures


def _rule(*clauses: RuleClause) -> PayerRule:
    return PayerRule(payer_slug="uhc", cpt_code="27447", clauses=tuple(clauses))


def test_delegated_clause_is_never_selected(scan):
    rule = _rule(DELEGATED)
    clause, addressed, method = rule.select_clause(scan[1], NOTE)
    assert clause is None
    assert method == "none"


def test_text_matching_cannot_reach_a_delegated_clause(scan):
    """The text matcher runs over every clause, advisory ones included.

    Without an explicit exclusion it could pick the delegated clause and hand
    back something with no requirements — which scores as though the payer had
    asked for nothing at all.
    """
    delegated_worded_like_the_note = RuleClause(
        polarity="delegated",
        indication_text="total knee arthroplasty osteoarthritis physical therapy",
        source_snippet="refer to the InterQual CP: Procedures",
        advisory=True,
    )
    rule = _rule(delegated_worded_like_the_note)
    clause, _, method = rule.select_clause(scan[1], NOTE)
    assert clause is None
    assert method != "text"


def test_status_says_delegated_not_adjudicated(scan):
    parsed, diagnoses, procedures = scan
    assessment = evaluate(
        parsed, diagnoses, procedures[0] if procedures else None, payer_rule=_rule(DELEGATED)
    )
    assert assessment.coverage_status is CoverageStatus.CRITERIA_DELEGATED


def test_delegation_is_surfaced_to_the_clinician(scan):
    parsed, diagnoses, procedures = scan
    assessment = evaluate(
        parsed, diagnoses, procedures[0] if procedures else None, payer_rule=_rule(DELEGATED)
    )
    assert assessment.advisories
    advisory = assessment.advisories[0]
    assert "does not publish criteria" in advisory
    # Worded as a limitation of the score, not as a restriction on the patient.
    assert "confirm this does not describe your patient" not in advisory


def test_the_rationale_does_not_claim_the_payers_criteria(scan):
    """The number and the caveat must not contradict each other.

    "Approval likelihood 69% against UHC's current criteria" sitting two lines
    above "UHC does not publish criteria for this procedure" is worse than
    either alone: a reader who skims the first sentence takes away the opposite
    of what the response says.
    """
    parsed, diagnoses, procedures = scan
    assessment = evaluate(
        parsed, diagnoses, procedures[0] if procedures else None, payer_rule=_rule(DELEGATED)
    )
    assert "UHC's current criteria" not in assessment.rationale
    assert "national baseline criteria" in assessment.rationale


def test_an_exclusion_still_denies_alongside_a_deferral(scan):
    """UHC publishes its exclusions even where it delegates its coverage rules.

    An excluded indication must stay excluded: not knowing the approval criteria
    is no reason to stop applying the denial criteria we do have.
    """
    knee_pain_only = [
        CodeCandidate(
            code="M25.562",
            description="Pain in left knee",
            system="ICD-10-CM",
            matched_text="knee pain",
            confidence=0.9,
        )
    ]
    rule = _rule(DELEGATED, EXCLUDED)
    clause, addressed, method = rule.select_clause(knee_pain_only, NOTE)
    assert clause is EXCLUDED
    assert method == "icd10"


def test_unknown_polarity_from_live_fails_safe():
    """Live and Scan deploy independently, so Scan meets polarities it predates.

    Defaulting an unrecognised one to "covered" would score a request against a
    clause whose meaning this build just admitted to not knowing.
    """
    assert _polarity("covered") == "covered"
    assert _polarity("excluded") == "excluded"
    assert _polarity("delegated") == "delegated"
    assert _polarity(None) == "covered"
    assert _polarity("conditionally_covered_pending_review") == "delegated"

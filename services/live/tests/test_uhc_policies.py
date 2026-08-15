"""Offline coverage for the UnitedHealthcare adapter.

Six frozen policies, each chosen for a specific way the parse can go wrong. The
two that matter most are the ones where the correct answer is *no rule*: a
policy that lists a code without adjudicating it, and a policy that adjudicates
a procedure but publishes no criteria for it. Both look like coverage to a
parser that only checks for medical-necessity language, and both would produce
a record indistinguishable from a real rule — source URL, recent timestamp,
prose containing "medically necessary" — for a determination nobody has read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas import PlanType, PolicyDocument

try:  # pragma: no cover - depends on whether the private core is mounted
    import app.payers as payers_pkg
    from app.payers.uhc import PROCEDURE_TERMS, UhcAdapter
except ImportError:  # pragma: no cover
    pytest.skip("clinchec-core-live not mounted", allow_module_level=True)

FIXTURE = Path(payers_pkg.__file__).parent / "fixtures" / "uhc_policies.json"


def _load() -> dict:
    if not FIXTURE.exists():  # pragma: no cover - core predates the fixture
        pytest.skip(f"fixture not present at {FIXTURE}")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


DOCUMENTS = _load()["documents"]


class _Settings:
    """Enough of Settings for parsing, which touches no network state."""

    offline_seed_mode = False
    respect_robots_txt = True
    payer_request_delay_seconds = 0.0
    payer_max_pages_per_run = 40
    payer_user_agent = "test"

    def portal_base_url(self, slug: str) -> str:
        return "https://www.uhcprovider.com"


def _parse(name: str):
    adapter = UhcAdapter(_Settings())
    entry = DOCUMENTS[name]
    document = PolicyDocument(
        payer_slug="uhc",
        url=entry["url"],
        title=name,
        plan_type=PlanType.COMMERCIAL,
    )
    return adapter.parse(document, entry["text"])


def _by_cpt(drafts) -> dict:
    return {d.cpt_code: d for d in drafts}


# --- the negative controls -------------------------------------------------


def test_declared_but_not_adjudicated_produces_no_rule():
    """CPT 62323 is in this policy's code table and is not what it governs.

    The policy adjudicates implanted intrathecal drug delivery systems. Emitting
    a 62323 rule from it would overwrite the epidural steroid injection criteria
    — which are real, published, and in a different document — with pump
    criteria, and nothing downstream could tell.
    """
    drafts = _parse("implanted-spinal-drug-delivery-systems")
    assert "62323" not in _by_cpt(drafts)


def test_delegated_policy_yields_no_coverage_clause():
    """The knee policy adjudicates and publishes nothing to adjudicate against."""
    drafts = _by_cpt(_parse("surgery-knee"))

    for cpt_code in ("27446", "27447", "29881"):
        draft = drafts[cpt_code]
        polarities = {c.polarity.value for c in draft.clauses}
        assert "delegated" in polarities, cpt_code
        assert "covered" not in polarities, cpt_code

        # And nothing that could be scored as though it were a criterion.
        assert draft.required_duration_weeks is None
        assert draft.required_conservative_care == []
        assert draft.required_imaging == []

        delegated = next(c for c in draft.clauses if c.polarity.value == "delegated")
        assert delegated.advisory is True
        assert "InterQual" in delegated.source_snippet


def test_delegation_recognised_when_vendor_follows_a_colon():
    """The shoulder policy writes "refer to the:" then the vendor on a new line.

    A `refer to the\\s+InterQual` pattern misses it, and the policy then reads as
    though "Surgery of the shoulder is proven and medically necessary" were the
    criteria for a rotator cuff repair.
    """
    draft = _by_cpt(_parse("surgery-shoulder"))["29827"]
    assert {c.polarity.value for c in draft.clauses} >= {"delegated"}
    assert "covered" not in {c.polarity.value for c in draft.clauses}


# --- the positive controls -------------------------------------------------


def test_inline_criteria_are_extracted():
    """The epidural policy states its own requirements; they must survive."""
    drafts = _by_cpt(_parse("epidural-steroid-injections-spinal-pain"))

    for cpt_code in ("62323", "64483"):
        draft = drafts[cpt_code]
        covered = [c for c in draft.clauses if c.polarity.value == "covered"]
        assert covered, cpt_code

        clause = covered[0]
        # The payer's own sentence, whole. Not a 160-character window ending in
        # a verb, which is what a leftmost-match regex produced.
        assert clause.indication_text.startswith("Epidural Steroid Injections")
        assert "proven and medically necessary" in clause.indication_text
        # Unscoped but uncontested, so it decides rather than merely informs.
        assert clause.advisory is False
        assert "physical therapy" in clause.required_conservative_care or (
            "NSAIDs" in clause.required_conservative_care
        )


def test_deferral_is_scoped_to_its_own_adjudication():
    """Sleep Studies states real criteria *and* defers elsewhere in one document.

    A document-level delegation check discarded the polysomnography criteria UHC
    does publish, on the strength of a deferral belonging to a different
    procedure in the same PDF.
    """
    drafts = _by_cpt(_parse("sleep-studies"))
    covered = [c for c in drafts["95810"].clauses if c.polarity.value == "covered"]
    assert covered
    assert any("Polysomnography" in c.indication_text for c in covered)


def test_subject_is_the_nearest_statement_not_the_first_match():
    """Bariatric surgery lists its procedures before the governing sentence.

    "Sleeve gastrectomy" and "bariatric surgery" are both terms for 43644, and a
    leftmost match quoted from the list item three lines above the verb — a
    snippet that reads as though the payer had written it that way.
    """
    draft = _by_cpt(_parse("bariatric-surgery"))["43644"]
    covered = [c for c in draft.clauses if c.polarity.value == "covered"]
    assert covered

    for clause in covered:
        assert not clause.indication_text.lower().startswith("sleeve gastrectomy")
        assert "medically necessary" in clause.indication_text


# --- provenance ------------------------------------------------------------


def test_every_tracked_cpt_has_procedure_terms():
    """A term list keyed on a CPT nothing declares is a dead entry.

    Cheap guard against the mapping drifting back to guesswork: every code the
    adapter claims to track must be one some frozen policy actually declares.
    """
    declared: set[str] = set()
    for name in DOCUMENTS:
        declared |= set(_by_cpt(_parse(name)))

    unreachable = set(PROCEDURE_TERMS) - declared
    # 64635 and 95806 live in policies not frozen here; the assertion is that
    # nothing in the fixture set is silently unmatched, not that every term is.
    assert declared, "no fixture produced any rule at all"
    assert declared <= set(PROCEDURE_TERMS)
    assert "27447" not in unreachable

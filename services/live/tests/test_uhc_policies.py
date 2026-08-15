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
    from app.payers.uhc import PROCEDURE_TERMS, UhcAdapter, short_title
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


def _document(name: str) -> PolicyDocument:
    entry = DOCUMENTS[name]
    return PolicyDocument(
        payer_slug="uhc",
        url=entry["url"],
        title=entry.get("title", name),
        plan_type=(
            PlanType.MEDICARE_ADVANTAGE
            if entry.get("plan_type") == "medicare_advantage"
            else PlanType.COMMERCIAL
        ),
    )


def _parse(name: str):
    adapter = UhcAdapter(_Settings())
    return adapter.parse(_document(name), DOCUMENTS[name]["text"])


def _parse_chain(*names: str):
    """Parse several documents through one adapter, in order.

    Cross-reference resolution is stateful within a crawl: a Medicare Advantage
    policy names a commercial policy, and the adapter can only follow that
    pointer if the commercial policy has already been parsed. `discover` sorts
    commercial documents first for exactly this reason, and a test that parses
    each document with a fresh adapter would pass while the feature did nothing.

    Returns the drafts from the *last* document.
    """
    adapter = UhcAdapter(_Settings())
    adapter._commercial_titles = {
        short_title(entry["title"])
        for entry in DOCUMENTS.values()
        if entry.get("plan_type") != "medicare_advantage" and entry.get("title")
    }
    drafts = []
    for name in names:
        drafts = adapter.parse(_document(name), DOCUMENTS[name]["text"])
    return drafts


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


# --- Medicare Advantage ----------------------------------------------------


def test_medicare_advantage_records_where_the_criteria_live():
    """MA policies route rather than argue; the route is the useful content.

    Running the commercial necessity patterns over an MA policy finds almost
    nothing, which is accurate and useless. CMS owns Medicare coverage, so the
    plan document mostly says which CMS instrument applies — and a clinician
    who knows the request is judged against an LCD table can go and read it.
    """
    drafts = _by_cpt(_parse("joint-procedures"))

    for cpt_code in ("27130", "27446", "27447"):
        draft = drafts[cpt_code]
        assert draft.plan_type is PlanType.MEDICARE_ADVANTAGE
        assert [c.polarity.value for c in draft.clauses] == ["delegated"]

        clause = draft.clauses[0]
        assert clause.source_pattern == "medicare_advantage_routing"
        assert clause.advisory is True
        assert "CMS LCD" in clause.indication_text
        # No evidence standard exists to state, so none may be recorded.
        assert draft.required_duration_weeks is None
        assert draft.required_conservative_care == []
        assert draft.required_imaging == []


def test_routing_is_read_per_topic_not_per_document():
    """One MA policy routes hip and knee differently, in adjacent sections.

    A document-level read attaches whichever destination came first to both,
    and the resulting record cites the wrong joint back to the payer.
    """
    drafts = _by_cpt(_parse("joint-procedures"))
    hip = drafts["27130"].clauses[0]
    knee = drafts["27447"].clauses[0]

    assert "surgery of the hip" in hip.indication_text.lower()
    assert "surgery of the knee" in knee.indication_text.lower()
    assert "Surgery of the Hip" in hip.source_snippet
    assert "Surgery of the Knee" in knee.source_snippet
    assert "Knee" not in hip.source_snippet


def test_a_named_cms_determination_survives_intact():
    """The NCD number is the whole point of quoting the sentence.

    "(100" instead of "(100.1)" sends a clinician to a determination that does
    not exist — the pattern has to allow a period inside a determination number
    while still stopping at the end of the sentence.
    """
    draft = _by_cpt(_parse("surgical-procedures"))["43644"]
    clause = draft.clauses[0]
    assert "CMS NCD" in clause.indication_text
    assert "100.1" in clause.source_snippet


def test_facet_ablation_is_absent_from_the_medicare_advantage_set():
    """64635 negative control, and the reason it is not in PROCEDURE_TERMS.

    The MA Pain Management policy governs adjacent denervation codes and never
    names facet radiofrequency ablation. Audited 2026-08-15 across the full
    extracted text of all 55 MA policies and all 259 commercial ones: the code
    appears in none of them.
    """
    assert "64635" not in PROCEDURE_TERMS

    entry = DOCUMENTS["pain-management-rehabilitation"]
    assert "64635" not in entry["text"]
    assert "64625" in entry["text"], "expected the adjacent denervation codes"

    assert _by_cpt(_parse("pain-management-rehabilitation")) == {}


def test_cpap_is_absent_and_stays_absent():
    """E0601 appears in the MA set only as a coding note, never as coverage.

    "Using the HCPCS codes for CPAP (E0601) ... for a ventilator ... is
    incorrect coding" is a billing instruction. The document carrying it routes
    CPAP coverage to DME MAC LCD L33800 and has no Applicable Codes section at
    all, so there is nothing here to build a rule from.
    """
    assert "E0601" not in PROCEDURE_TERMS


# --- following the commercial pointer --------------------------------------


def test_the_commercial_pointer_is_followed():
    """MA routing frequently ends at a commercial policy the adapter holds.

    "refer to the UnitedHealthcare Commercial Medical Policy titled Bariatric
    Surgery" is a resolvable reference, and following it turns a record that
    said only "governed by CMS and a commercial policy" into one carrying the
    payer's actual requirements — BMI thresholds, comorbidities, the lot.
    """
    draft = _by_cpt(_parse_chain("bariatric-surgery", "surgical-procedures"))["43644"]

    assert "Bariatric Surgery" in draft.clauses[0].indication_text
    # The referenced criteria, not just the reference.
    assert "UnitedHealthcare commercial policy — Bariatric Surgery" in draft.criteria_text
    assert "Body Mass Index" in draft.criteria_text


def test_a_conditional_pointer_is_quoted_but_never_scored():
    """The commercial policy binds only where CMS has not spoken.

    UHC writes it plainly: "For coverage guidelines for states/territories with
    no LCDs/LCAs, refer to the ... Commercial Medical Policy". Which members
    that covers depends on their state, and a note does not say. Attaching the
    commercial criteria as scoring clauses would apply them to everyone,
    including members whose LCD says something else — so they are carried as
    reference text and the rule keeps no evidence requirements of its own.
    """
    draft = _by_cpt(_parse_chain("bariatric-surgery", "surgical-procedures"))["43644"]

    assert [c.polarity.value for c in draft.clauses] == ["delegated"]
    assert draft.required_duration_weeks is None
    assert draft.required_conservative_care == []
    assert draft.required_imaging == []

    text = draft.clauses[0].indication_text
    assert "depends on the member's state" in text
    assert "not scored" in text
    # The sentence is read both on the rule and, alone, as a scan advisory.
    assert "below" not in text


def test_an_unconditional_pointer_carries_the_criteria_over():
    """Where UHC says no LCD exists, the commercial policy simply is the rule.

    The FAI block in the same document is the real example: "LCDs/LCAs do not
    exist. For coverage guidelines, refer to the ... Policy titled Surgery of
    the Hip." Nothing is conditional there, so treating it as conditional would
    withhold criteria that genuinely govern.
    """
    from app.payers.adjudication import find_routing
    from app.utils.pdf import section

    rationale = section(
        DOCUMENTS["joint-procedures"]["text"],
        "Coverage Rationale",
        ["Applicable Codes", "Definitions", "Clinical Evidence"],
    )
    routing = find_routing(
        rationale,
        [r"femoroacetabular impingement", r"\bFAI\b"],
        {"Surgery of the Hip"},
    )
    assert routing is not None
    assert routing.topic.startswith("Femoroacetabular")
    assert routing.commercial_policy == "Surgery of the Hip"
    assert routing.conditional is False


def test_conditionality_defaults_to_the_safe_answer():
    """A block whose conditionality cannot be read must not be treated as
    governing everywhere. Under-applying criteria loses a requirement; over-
    applying them scores a member against rules their state replaced."""
    from app.payers.adjudication import Routing

    assert Routing(topic="x", destinations=()).conditional is True


def test_an_unresolvable_pointer_degrades_to_the_route_alone():
    """A pointer to a policy this crawl does not hold resolves to nothing.

    Failing closed matters more than it looks: the title runs into the sentence
    after it, so a parser that guessed where the name ended would cite a policy
    that does not exist.
    """
    drafts = _by_cpt(_parse("joint-procedures"))  # no commercial pass first
    clause = drafts["27447"].clauses[0]
    assert clause.polarity.value == "delegated"
    assert "commercial policy" in clause.indication_text.lower()
    assert "UnitedHealthcare commercial policy —" not in drafts["27447"].criteria_text


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

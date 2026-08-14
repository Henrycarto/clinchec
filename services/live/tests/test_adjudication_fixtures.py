"""Offline coverage for the adjudication pattern registry.

`find_adjudications()` answers the question that decides whether a payer rule is
usable: does this bulletin's Policy text actually *adjudicate* this CPT, or does
it merely *mention* it?

The distinction is not academic. CPB 0743 declares 72148 (lumbar MRI) in its
codes table, and its Policy section says:

    "Lumbar laminectomy ... is considered medically necessary when all of the
     following criteria are met: ... significant pathology ... on the advanced
     imaging radiology report"

The subject is laminectomy; MRI is the evidence that qualifies a patient for
surgery. Mapping 72148 to 0743 would score an MRI request against surgical
criteria that require the patient to already have the imaging being requested.
The bulletin that governs lumbar MRI is 0236.

These cases were verified against live pages and are frozen here so the
registry is exercised offline. The two negative controls are the point: a test
suite that only checks the positive cases would pass on a registry that says
"governed" to everything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.payers as payers_pkg
from app.payers.adjudication import find_adjudications, policy_text

FIXTURE = Path(payers_pkg.__file__).parent / "fixtures" / "aetna_cpb_regions.json"


def _load() -> dict:
    if not FIXTURE.exists():  # pragma: no cover - core not mounted
        pytest.skip(f"fixture not present at {FIXTURE}")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


FIXTURES = _load()
PAGES = FIXTURES["pages"]
CASES = FIXTURES["adjudication_cases"]
CASE_IDS = [f"{c['bulletin']}->{c['cpt']}" for c in CASES]


def _polarity(found) -> str:  # noqa: ANN001
    if not found:
        return "none"
    pols = {f.polarity for f in found}
    if pols == {"positive", "negative"}:
        return "mixed"
    return "positive" if pols == {"positive"} else "negative"


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_governance_verdict_matches_the_verified_result(case: dict) -> None:
    body = PAGES[case["bulletin"]]["policy_marked"]
    found = find_adjudications(body, case["terms"])
    assert _polarity(found) == case["expect"], (
        f"CPB {case['bulletin']} -> {case['cpt']}: "
        f"expected {case['expect']}, got {_polarity(found)}"
    )


# --- the controls, named explicitly so a regression is unambiguous ----------

def _case(bulletin: str, cpt: str) -> dict:
    return next(c for c in CASES if c["bulletin"] == bulletin and c["cpt"] == cpt)


def test_cited_only_is_not_governed() -> None:
    """CPB 0743 mentions MRI as evidence for laminectomy — it does not govern it."""
    case = _case("0743", "72148")
    found = find_adjudications(PAGES["0743"]["policy_marked"], case["terms"])
    assert found == [], f"0743 wrongly governs 72148 via {[f.pattern for f in found]}"


def test_declared_but_unadjudicated_code_is_not_governed() -> None:
    """CPB 0736 lists 27130 in its codes table but never adjudicates it."""
    case = _case("0736", "27130")
    found = find_adjudications(PAGES["0736"]["policy_marked"], case["terms"])
    assert found == []


def test_the_correct_bulletin_does_govern_lumbar_mri() -> None:
    case = _case("0236", "72148")
    found = find_adjudications(PAGES["0236"]["policy_marked"], case["terms"])
    assert found, "0236 should govern 72148"
    assert any("medically necessary" in f.snippet.lower() for f in found)


def test_enumerated_list_construction_is_recognised() -> None:
    """Aetna's house style: "considers the following medically necessary: <list>".

    The first version of the registry missed this entirely, because the
    procedure sits in a list item after a colon rather than adjacent to the
    predicate. It is the most common construction in the corpus, not an edge
    case.
    """
    case = _case("0673", "29881")
    found = find_adjudications(PAGES["0673"]["policy_marked"], case["terms"])
    patterns = {f.pattern for f in found}
    assert "header_list_positive" in patterns
    assert "header_list_negative" in patterns


def test_nested_criteria_are_not_read_as_governed_procedures() -> None:
    """List nesting depth separates a procedure from its criteria.

    Aetna nests criteria inside list items:

        considers the following medically necessary:
          <li> Lumbar laminectomy ... when all of the following are met:
               <ul><li> Advanced imaging (CT or MRI) indicates stenosis </li></ul>

    Flattening made that inner criterion look like a peer procedure, which
    briefly resurrected the 0743/72148 false positive. Depth tracking fixed it.
    """
    found = find_adjudications(PAGES["0743"]["policy_marked"], [r"advanced imaging"])
    assert found == [], (
        "a nested criterion was read as an adjudicated procedure: "
        f"{[f.snippet[:80] for f in found]}"
    )


def test_mixed_polarity_is_represented_not_collapsed() -> None:
    """One CPT can be approved for one indication and excluded for another.

    CPB 0673 approves partial meniscectomy for mechanical symptoms with mild OA
    and calls it experimental for meniscal root tears. Collapsing that to a
    single boolean is what the indication-scoped schema exists to fix.
    """
    found = find_adjudications(PAGES["0673"]["policy_marked"], _case("0673", "29881")["terms"])
    assert {f.polarity for f in found} == {"positive", "negative"}


def test_registry_covers_every_construction_the_corpus_uses() -> None:
    """Every declared pattern should fire somewhere, or it is dead code."""
    fired: set[str] = set()
    for case in CASES:
        for found in find_adjudications(PAGES[case["bulletin"]]["policy_marked"], case["terms"]):
            fired.add(found.pattern)
    assert {"direct_subject", "considers_inline",
            "header_list_positive", "header_list_negative"} <= fired


def test_policy_text_extraction_is_deterministic() -> None:
    """The fixture must match what the extractor produces from the same page."""
    if not (Path("/tmp/cpb_cache") / "0660.html").exists():
        pytest.skip("live corpus not cached; fixture-only run")
    assert policy_text("0660") == PAGES["0660"]["policy_marked"]

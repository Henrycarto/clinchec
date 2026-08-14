"""Adapters emit indication-scoped clauses from real policy structure.

The adjudication registry was validated in isolation. These tests prove it is
actually wired into the adapter output — that a bulletin which both approves and
excludes the same CPT produces two clauses of opposite polarity, from HTML
shaped the way Aetna shapes it.

The scoping rule is the part that matters most. Policy prose names indications
in words, not codes:

    "Meniscectomy ... for the treatment of medial or lateral meniscal root tears"

An exclusion carrying no ICD-10 scope matches every request by construction, so
emitting one would deny every meniscectomy. Those clauses are marked advisory
instead: shown to the clinician, never allowed to decide a score.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import app.payers as payers_pkg
from app.config import Settings
from app.payers import build_adapter
from app.payers.base import PayerAdapter
from app.schemas import Polarity

FIXTURE = pathlib.Path(payers_pkg.__file__).parent / "fixtures" / "aetna_cpb_regions.json"

# A CPB Policy section shaped the way Aetna shapes them: an h2 anchor, an
# enumerated coverage list, then an exclusion list. Codes appear in the first
# clause and not the second, which is the realistic asymmetry.
POLICY_HTML = """
<html><body>
<h1>Arthroscopic Lavage and Debridement</h1>
<h2>Table Of Contents</h2>
<h2>Policy</h2>
  <h3>Medical Necessity</h3>
  <p>Aetna considers the following medically necessary:</p>
  <ul>
    <li>Arthroscopic knee surgery (with or without partial meniscectomy) for
        persons with knee pain plus mechanical symptoms (M23.221) when all of
        the following criteria are met:
      <ul>
        <li>At least 12 weeks of physical therapy</li>
        <li>Radiographs exclude advanced osteoarthritis</li>
      </ul>
    </li>
  </ul>
  <h3>Experimental, Investigational, or Unproven</h3>
  <p>Aetna considers the following procedures experimental, investigational, or
     unproven:</p>
  <ul>
    <li>Meniscectomy (arthroscopic or open; total or partial) for the treatment
        of medial or lateral meniscal root tears</li>
  </ul>
<h2>Background</h2>
  <p>Arthroscopic surgery has been studied extensively. Katz et al. reported
     that at least 24 months of follow-up showed no benefit over physical
     therapy, and considered the procedure medically necessary only in
     selected populations.</p>
</body></html>
"""

MENISCUS_TERMS = [r"meniscect", r"arthroscopic knee surgery"]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        offline_seed_mode=True,
        database_url="postgresql+asyncpg://x:x@localhost/x",
    )


def _clauses() -> list:
    from app.payers.adjudication import policy_section_marked

    marked = policy_section_marked(POLICY_HTML)
    return PayerAdapter.clauses_from_policy(marked, MENISCUS_TERMS)


# --- extraction from real structure ----------------------------------------

def test_both_polarities_are_extracted() -> None:
    clauses = _clauses()
    assert {c.polarity for c in clauses} == {Polarity.COVERED, Polarity.EXCLUDED}


def test_coverage_clause_carries_its_evidence_requirements() -> None:
    covered = next(c for c in _clauses() if c.polarity is Polarity.COVERED)
    assert covered.required_duration_weeks == 12
    assert "physical therapy" in covered.required_conservative_care


def test_exclusion_carries_no_evidence_requirements() -> None:
    """An exclusion denies on indication alone; evidence cannot overturn it."""
    excluded = next(c for c in _clauses() if c.polarity is Polarity.EXCLUDED)
    assert excluded.required_duration_weeks is None
    assert excluded.required_conservative_care == []
    assert excluded.required_imaging == []


def test_indication_text_is_the_payers_own_wording() -> None:
    excluded = next(c for c in _clauses() if c.polarity is Polarity.EXCLUDED)
    assert "meniscal root tears" in excluded.indication_text.lower()
    assert excluded.source_pattern.startswith("header_list")


def test_background_prose_does_not_leak_into_clauses() -> None:
    """Background says "medically necessary" while reviewing trials."""
    for clause in _clauses():
        assert "Katz" not in clause.indication_text
        assert "follow-up" not in clause.indication_text


# --- the scoping rule -------------------------------------------------------

def test_unscoped_clause_is_marked_advisory() -> None:
    """No ICD codes in the sentence means we cannot say when it applies."""
    excluded = next(c for c in _clauses() if c.polarity is Polarity.EXCLUDED)
    assert excluded.indication_icd10_prefixes == []
    assert excluded.advisory is True


def test_scoped_clause_is_not_advisory() -> None:
    covered = next(c for c in _clauses() if c.polarity is Polarity.COVERED)
    assert "M23.221" in covered.indication_icd10_prefixes
    assert covered.advisory is False


def test_duplicate_adjudications_are_collapsed() -> None:
    """Several patterns can fire on one sentence; storing both double-counts it."""
    clauses = _clauses()
    keys = [(c.polarity, " ".join(c.indication_text.lower().split())) for c in clauses]
    assert len(keys) == len(set(keys))


# --- wired into the adapter -------------------------------------------------

def test_aetna_seed_rules_carry_the_mixed_case(settings) -> None:
    """CPB 0673 approves and excludes the same CPT; the seed must show both."""
    adapter = build_adapter("aetna", settings)
    draft = next(d for d in adapter.seed_rules() if d.cpt_code == "29881")

    assert {c.polarity for c in draft.clauses} == {Polarity.COVERED, Polarity.EXCLUDED}


def test_root_tear_exclusion_is_advisory_because_icd10_cannot_scope_it(
    settings,
) -> None:
    """Some payer distinctions are finer than the code set.

    Aetna covers arthroscopic meniscectomy for mechanical symptoms and excludes
    it for meniscal root tears — but both code to M23.2xx. No prefix set
    separates them, so any scope claimed for this exclusion is invented, and
    would either deny every meniscectomy or silently approve the excluded
    indication.

    The clause is therefore advisory: shown to the clinician as a question,
    never applied to the score.
    """
    adapter = build_adapter("aetna", settings)
    draft = next(d for d in adapter.seed_rules() if d.cpt_code == "29881")

    excluded = next(c for c in draft.clauses if c.polarity is Polarity.EXCLUDED)
    assert excluded.advisory is True
    assert excluded.indication_icd10_prefixes == [], (
        "an ICD scope here would be fabricated — root tears and mechanical-symptom "
        "tears share the M23.2xx family"
    )

    covered = next(c for c in draft.clauses if c.polarity is Polarity.COVERED)
    assert covered.advisory is False
    assert covered.indication_icd10_prefixes


def test_clauses_move_the_checksum(settings) -> None:
    """A payer adding an exclusion must register as a rule change."""
    adapter = build_adapter("aetna", settings)
    draft = next(d for d in adapter.seed_rules() if d.cpt_code == "29881")
    before = draft.checksum()

    draft.clauses = [c for c in draft.clauses if c.polarity is not Polarity.EXCLUDED]
    assert draft.checksum() != before, (
        "dropping an exclusion did not change the checksum, so a payer lifting "
        "a restriction would sync silently"
    )


def test_other_seed_rules_still_parse(settings) -> None:
    adapter = build_adapter("aetna", settings)
    seeds = adapter.seed_rules()
    assert len(seeds) >= 4
    for seed in seeds:
        assert seed.cpt_code
        for clause in seed.clauses:
            assert clause.indication_text
            assert clause.source_snippet


# --- against the real cached corpus ----------------------------------------

@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not present")
def test_real_cpb_0673_yields_both_polarities() -> None:
    """The same assertion, against text captured from the live bulletin."""
    fixtures = json.loads(FIXTURE.read_text(encoding="utf-8"))
    marked = fixtures["pages"]["0673"]["policy_marked"]

    clauses = PayerAdapter.clauses_from_policy(marked, MENISCUS_TERMS)
    assert {c.polarity for c in clauses} == {Polarity.COVERED, Polarity.EXCLUDED}

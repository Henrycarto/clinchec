"""Text-based indication matching.

The case this exists for: Aetna CPB 0673 covers arthroscopic meniscectomy for
mechanical symptoms and excludes it for meniscal root tears. Both code to
M23.2xx, so ICD-10 cannot separate them and clause selection by code alone
either denies every meniscectomy or silently approves the excluded indication.

Roughly half these tests assert that the matcher **abstains**. That is the
point. A confident wrong match scores a request against criteria the payer does
not apply to it, which is worse than no match at all — the caller falls back to
code scoping or reports the indication is not addressed.
"""

from __future__ import annotations

import pytest

from app.models.indication_matcher import (
    MIN_DISTINCTIVE_HITS,
    MIN_MARGIN,
    distinctive_terms,
    match,
)

COVERED = (
    "Arthroscopic knee surgery (with or without partial meniscectomy or meniscal "
    "repair) for persons presenting with significant knee pain plus mechanical "
    "symptoms and no more than mild osteoarthritis (Kellgren-Lawrence 0, 1 or 2)"
)
EXCLUDED = (
    "Meniscectomy (arthroscopic or open; total or partial) for the treatment of "
    "medial or lateral meniscal root tears"
)
CLAUSES = [COVERED, EXCLUDED]

MECHANICAL_NOTE = (
    "44 y/o male with right knee pain and mechanical locking for 4 months. "
    "Failed 12 weeks of physical therapy. Radiographs show no advanced "
    "osteoarthritis. Medial meniscus tear, right knee."
)
ROOT_TEAR_NOTE = (
    "51 y/o female with right knee pain for 5 months. Failed 12 weeks of "
    "physical therapy. MRI shows a medial meniscal root tear. "
    "Medial meniscal root tear, right knee."
)


# --- distinctive vocabulary -------------------------------------------------

def test_shared_vocabulary_is_discarded() -> None:
    """"Meniscectomy" is in both clauses and says nothing about which applies."""
    covered_terms, excluded_terms = distinctive_terms(CLAUSES)
    assert "meniscectomy" not in covered_terms
    assert "meniscectomy" not in excluded_terms
    assert not covered_terms & excluded_terms


def test_discriminating_vocabulary_survives() -> None:
    covered_terms, excluded_terms = distinctive_terms(CLAUSES)
    assert any("mechanical" in t for t in covered_terms)
    assert any("root" in t for t in excluded_terms)


def test_a_single_clause_has_nothing_to_discriminate_against() -> None:
    assert distinctive_terms([COVERED]) == [set()]


# --- the case this was built for -------------------------------------------

def test_root_tear_note_selects_the_exclusion() -> None:
    result = match(CLAUSES, ROOT_TEAR_NOTE)
    assert result is not None, "root tear language should be decisive"
    assert result.clause_index == 1
    assert any("root" in t for t in result.matched_terms)


def test_mechanical_symptom_note_selects_coverage() -> None:
    result = match(CLAUSES, MECHANICAL_NOTE)
    assert result is not None
    assert result.clause_index == 0


def test_the_two_notes_reach_opposite_clauses() -> None:
    """The whole point — same CPT, same code family, opposite answers."""
    root = match(CLAUSES, ROOT_TEAR_NOTE)
    mech = match(CLAUSES, MECHANICAL_NOTE)
    assert root is not None and mech is not None
    assert root.clause_index != mech.clause_index


# --- abstention -------------------------------------------------------------

def test_abstains_on_a_note_that_names_neither_indication() -> None:
    result = match(CLAUSES, "Patient seen today for routine follow up. Doing well.")
    assert result is None


def test_abstains_on_an_ambiguous_note() -> None:
    """Language from both indications must not resolve to either."""
    ambiguous = "Knee pain with mechanical symptoms and a medial meniscal root tear."
    result = match(CLAUSES, ambiguous)
    if result is not None:
        assert result.margin >= MIN_MARGIN, (
            "a note naming both indications was resolved on too thin a margin"
        )


def test_abstains_on_a_single_coincidental_term() -> None:
    """One shared word is coincidence, not evidence."""
    result = match(CLAUSES, "The patient reports knee pain.")
    assert result is None


def test_abstains_on_empty_input() -> None:
    assert match(CLAUSES, "") is None
    assert match(CLAUSES, "   ") is None


def test_abstains_when_there_is_nothing_to_choose_between() -> None:
    assert match([COVERED], MECHANICAL_NOTE) is None
    assert match([], MECHANICAL_NOTE) is None


def test_thresholds_are_enforced_not_decorative() -> None:
    result = match(CLAUSES, ROOT_TEAR_NOTE)
    assert result is not None
    assert result.hits >= MIN_DISTINCTIVE_HITS
    assert result.margin >= MIN_MARGIN


# --- normalisation ----------------------------------------------------------

def test_plural_and_singular_match() -> None:
    """The payer writes "root tears"; the note writes "root tear"."""
    result = match(CLAUSES, ROOT_TEAR_NOTE)
    assert result is not None and result.clause_index == 1


def test_matching_is_case_insensitive() -> None:
    assert match(CLAUSES, ROOT_TEAR_NOTE.upper()) is not None


def test_matched_terms_are_reported_for_explainability() -> None:
    """A reviewer must be able to see why a clause was chosen."""
    result = match(CLAUSES, ROOT_TEAR_NOTE)
    assert result is not None
    assert result.matched_terms
    assert all(isinstance(t, str) and t for t in result.matched_terms)


# --- plan-section exclusion -------------------------------------------------

def test_plan_language_is_not_part_of_the_indication_text() -> None:
    """Plan names the requested procedure, which every clause mentions.

    Including it would pull toward whichever clause phrases the procedure the
    same way, which is noise rather than signal about *why* it is requested.
    """
    from app.models.indication_matcher import indication_text_of
    from app.models.soap_parser import get_parser

    note = (
        "S: 51 y/o female with knee pain. MRI shows a medial meniscal root tear.\n\n"
        "A: Medial meniscal root tear.\n\n"
        "P: Arthroscopic partial meniscectomy, right."
    )
    parsed = get_parser("en_core_web_sm").parse(note)
    text = indication_text_of(parsed)

    assert "root tear" in text.lower()
    assert "arthroscopic partial meniscectomy" not in text.lower()


@pytest.mark.parametrize("note", [MECHANICAL_NOTE, ROOT_TEAR_NOTE])
def test_matching_is_deterministic(note: str) -> None:
    first, second = match(CLAUSES, note), match(CLAUSES, note)
    assert (first is None) == (second is None)
    if first is not None and second is not None:
        assert first.clause_index == second.clause_index

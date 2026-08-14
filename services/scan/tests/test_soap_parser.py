"""Parser-level tests. These run without network and without a spaCy model
download — the blank-pipeline fallback covers everything except noun chunks.
"""

from __future__ import annotations

import pytest

from app.models.soap_parser import get_parser
from app.schemas import EntityLabel, Sex, SoapSection

NOTE_LUMBAR = """\
S: 47 y/o male presents with low back pain radiating into the right leg for 4 months.
He has completed 8 weeks of physical therapy and a course of NSAIDs without relief.
Denies bowel or bladder incontinence. Reports he is unable to work as a warehouse
loader and has difficulty walking more than one block.

O: Positive straight leg raise on the right. Lumbar x-ray shows disc space narrowing
at L5-S1. Strength 5/5 bilaterally.

A: Lumbar radiculopathy with suspected herniated disc at L5-S1.

P: Order MRI lumbar spine. Continue home exercise program. Follow up in 3 weeks.
"""

NOTE_KNEE = """\
Chief Complaint: right knee pain
HPI: 62-year-old female with a 14 month history of right knee pain. She has failed
physical therapy, a corticosteroid injection, and activity modification.
Exam: Weight-bearing films demonstrate bone-on-bone medial compartment narrowing.
Assessment: Right knee osteoarthritis, tricompartmental.
Plan: Total knee arthroplasty, right.
"""

NOTE_MINIMAL = "Patient seen today. Doing well. Follow up as needed."


@pytest.fixture(scope="module")
def parser():
    return get_parser("en_core_web_sm")


# --- Section segmentation --------------------------------------------------

def test_segments_all_four_soap_sections(parser):
    parsed = parser.parse(NOTE_LUMBAR)
    found = {s.section for s in parsed.sections}
    assert {
        SoapSection.SUBJECTIVE,
        SoapSection.OBJECTIVE,
        SoapSection.ASSESSMENT,
        SoapSection.PLAN,
    } <= found


def test_section_offsets_index_back_into_the_original_note(parser):
    parsed = parser.parse(NOTE_LUMBAR)
    for section in parsed.sections:
        assert NOTE_LUMBAR[section.start : section.end].strip() == section.text


def test_header_like_text_mid_sentence_does_not_split_the_note(parser):
    note = "S: Patient states A: no relief from rest. Plan discussed at length."
    parsed = parser.parse(note)
    # Only the line-anchored "S:" is a header.
    assert len([s for s in parsed.sections if s.section is SoapSection.ASSESSMENT]) == 0


# --- Demographics ----------------------------------------------------------

@pytest.mark.parametrize(
    ("note", "age", "sex"),
    [
        ("S: 47 y/o male with back pain for 4 months.", 47, Sex.MALE),
        ("62-year-old female presents with knee pain.", 62, Sex.FEMALE),
        ("Age: 33\nSex: F\nComplains of headache.", 33, Sex.FEMALE),
        ("A 8 month-old boy brought in by his mother.", 8, Sex.MALE),
    ],
)
def test_extracts_age_and_sex(parser, note, age, sex):
    demographics = parser.parse(note).demographics
    assert demographics.age == age
    assert demographics.sex is sex


def test_duration_phrase_is_not_mistaken_for_an_age(parser):
    demographics = parser.parse("Patient with knee pain for 3 months.").demographics
    assert demographics.age is None


def test_sex_falls_back_to_pronouns_with_lower_confidence(parser):
    demographics = parser.parse("Patient reports she has had knee pain.").demographics
    assert demographics.sex is Sex.FEMALE
    assert demographics.confidence < 0.8


# --- Chief complaint -------------------------------------------------------

def test_explicit_chief_complaint_header_wins(parser):
    parsed = parser.parse(NOTE_KNEE)
    assert parsed.chief_complaint == "right knee pain"


def test_chief_complaint_falls_back_to_presentation_verb(parser):
    parsed = parser.parse(NOTE_LUMBAR)
    assert parsed.chief_complaint is not None
    assert "low back pain" in parsed.chief_complaint


# --- Duration --------------------------------------------------------------

def test_extracts_condition_duration_in_weeks(parser):
    duration = parser.parse(NOTE_LUMBAR).duration
    assert duration is not None
    assert duration.unit == "months"
    assert duration.value == 4
    assert duration.normalized_weeks == pytest.approx(17.38, abs=0.05)


def test_prefers_symptom_duration_over_treatment_duration(parser):
    # The note mentions "8 weeks of physical therapy" — that must not win.
    duration = parser.parse(NOTE_LUMBAR).duration
    assert duration is not None
    assert duration.unit == "months"


def test_extracts_n_month_history_form(parser):
    duration = parser.parse(NOTE_KNEE).duration
    assert duration is not None
    assert duration.value == 14
    assert duration.unit == "months"


def test_no_duration_returns_none(parser):
    assert parser.parse(NOTE_MINIMAL).duration is None


# --- Entities --------------------------------------------------------------

def test_extracts_diagnosis_and_procedure_entities(parser):
    parsed = parser.parse(NOTE_LUMBAR)
    diagnoses = {e.normalized for e in parsed.asserted(EntityLabel.DIAGNOSIS)}
    procedures = {e.normalized for e in parsed.asserted(EntityLabel.PROCEDURE)}
    assert "low back pain" in diagnoses
    assert "mri lumbar spine" in procedures


def test_extracts_conservative_care(parser):
    parsed = parser.parse(NOTE_LUMBAR)
    care = {e.normalized for e in parsed.asserted(EntityLabel.CONSERVATIVE_CARE)}
    assert "physical therapy" in care


def test_negated_findings_are_flagged(parser):
    parsed = parser.parse("A: Denies bowel incontinence and saddle anesthesia.")
    red_flags = parser.parse("A: Denies bowel incontinence.").entities_with_label(
        EntityLabel.RED_FLAG
    )
    assert red_flags, "expected the red-flag term to be matched at all"
    assert all(e.negated for e in red_flags)
    assert not parsed.asserted(EntityLabel.RED_FLAG)


def test_negation_scope_stops_at_a_conjunction(parser):
    parsed = parser.parse(
        "S: Denies fever, but reports progressive weakness in the right leg."
    )
    affirmed = {e.normalized for e in parsed.asserted(EntityLabel.RED_FLAG)}
    assert "progressive weakness" in affirmed


def test_hedged_findings_are_flagged_uncertain(parser):
    parsed = parser.parse("A: Suspected herniated disc at L5-S1.")
    hedged = [e for e in parsed.entities if e.normalized == "herniated disc"]
    assert hedged and hedged[0].uncertain


def test_lowercase_pt_is_not_read_as_physical_therapy(parser):
    parsed = parser.parse("S: The pt reports low back pain for 6 weeks.")
    care = {e.normalized for e in parsed.asserted(EntityLabel.CONSERVATIVE_CARE)}
    assert "pt" not in care


def test_uppercase_PT_is_read_as_physical_therapy(parser):
    parsed = parser.parse("S: Completed PT with no relief. Low back pain for 6 weeks.")
    care = {e.normalized for e in parsed.asserted(EntityLabel.CONSERVATIVE_CARE)}
    assert "pt" in care


def test_longest_match_wins_over_substring(parser):
    parsed = parser.parse("P: Order MRI lumbar spine.")
    procedures = [e.normalized for e in parsed.asserted(EntityLabel.PROCEDURE)]
    assert "mri lumbar spine" in procedures
    assert "mri" not in procedures


@pytest.mark.parametrize(
    "phrasing",
    [
        "O: Weight-bearing radiographs show joint space narrowing.",
        "O: Weight-bearing radiograph shows joint space narrowing.",
        "O: Plain films show joint space narrowing.",
        "O: Plain film shows joint space narrowing.",
        "O: X-rays show joint space narrowing.",
    ],
)
def test_imaging_is_detected_in_singular_and_plural(parser, phrasing):
    """Clinicians write "radiograph" and "radiographs" interchangeably.

    Missing one form silently drops the prior-imaging driver, which moves the
    approval score by a whole band.
    """
    parsed = parser.parse(phrasing)
    assert parsed.asserted(EntityLabel.IMAGING_EVIDENCE), phrasing


def test_plural_diagnosis_still_resolves_to_a_code(parser):
    from app.models.icd_extractor import extract_codes

    note = "A: Bilateral migraines, worsening."
    parsed = parser.parse(note)
    diagnoses, _ = extract_codes(parsed.entities, note)
    assert any(d.code == "G43.909" for d in diagnoses)


def test_surface_variants_only_inflects_the_head_noun():
    from app.models.lexicon import surface_variants

    assert set(surface_variants("weight-bearing films")) == {
        "weight-bearing film",
        "weight-bearing films",
    }
    assert set(surface_variants("radiograph")) == {"radiograph", "radiographs"}
    # A hyphenated head inflects on its final segment.
    assert set(surface_variants("x-ray")) == {"x-ray", "x-rays"}
    # Heads that are too short or not alphabetic are left alone, not mangled.
    assert surface_variants("ct") == ["ct"]
    assert surface_variants("l5-s1") == ["l5-s1"]


def test_entity_offsets_index_back_into_the_note(parser):
    parsed = parser.parse(NOTE_KNEE)
    for entity in parsed.entities:
        assert NOTE_KNEE[entity.start : entity.end] == entity.text


def test_empty_ish_note_parses_without_error(parser):
    parsed = parser.parse(NOTE_MINIMAL)
    assert parsed.demographics.age is None
    assert parsed.duration is None

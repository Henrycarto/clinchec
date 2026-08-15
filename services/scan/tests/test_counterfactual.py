"""What documenting a missing element would be worth.

The product question is "does fixing this matter", and the score's own weights
answer it exactly — which is the whole reason it can be answered at all.

The line this must not cross: Clinchec has never observed a submitted request's
outcome. It has nothing to calibrate a probability against, so "raises approval
odds by 80%" would be invented in the same way the fabricated bulletin mappings
and the invented 80-character guard were. "Adds 26 points to a documentation
score" is arithmetic anyone can check against the driver list.
"""

from __future__ import annotations

import pytest

from app.models.icd_extractor import extract_codes
from app.models.scoring import _CEILING, evaluate
from app.models.soap_parser import get_parser

#: Documents the procedure and diagnosis and nothing else, so most drivers are
#: unmet and carry a counterfactual.
THIN = """\
S: 58 y/o male with knee pain.

O: Tender medially.

A: Knee osteoarthritis.

P: Total knee arthroplasty.
"""

#: The same request, fully documented.
COMPLETE = """\
S: 62 y/o female with right knee pain for 14 months. Completed 12 weeks of
physical therapy, a corticosteroid injection and activity modification without
relief. Unable to climb stairs and difficulty walking more than one block.

O: Weight-bearing radiographs show bone-on-bone medial compartment narrowing.
Antalgic gait.

A: Right knee osteoarthritis, tricompartmental, end stage.

P: Proceed with total knee arthroplasty, right.
"""


@pytest.fixture(scope="module")
def parser():
    return get_parser("en_core_web_sm")


def _assess(parser, note: str):
    parsed = parser.parse(note)
    diagnoses, procedures = extract_codes(parsed.entities, note)
    return evaluate(parsed, diagnoses, procedures[0] if procedures else None)


def test_unmet_drivers_say_what_documenting_them_is_worth(parser):
    assessment = _assess(parser, THIN)
    unmet = [d for d in assessment.drivers if not d.satisfied]
    assert unmet, "expected a thin note to leave drivers unmet"

    priced = [d for d in unmet if d.potential_delta is not None]
    assert priced, "no unmet driver carried a counterfactual"
    for driver in priced:
        assert driver.potential_delta > 0, driver.key


def test_the_counterfactual_is_the_swing_not_the_award(parser):
    """A driver at -0.13 that would pay +0.13 is worth 0.26, not 0.13.

    Telling a clinician "worth 13" when it is worth 26 understates the one
    number they act on, and does so in the direction that makes fixing the note
    look not worth the typing.
    """
    assessment = _assess(parser, THIN)
    for driver in assessment.drivers:
        if driver.satisfied or driver.potential_delta is None:
            continue
        expected = round(_CEILING[driver.key] - driver.delta, 3)
        assert driver.potential_delta == expected, driver.key
        # Which, for a driver carrying a penalty, exceeds the ceiling alone.
        if driver.delta < 0:
            assert driver.potential_delta > _CEILING[driver.key]


def test_satisfied_drivers_carry_no_counterfactual(parser):
    assessment = _assess(parser, COMPLETE)
    for driver in assessment.drivers:
        if driver.satisfied:
            assert driver.potential_delta is None, driver.key


def test_no_driver_can_exceed_its_advertised_ceiling(parser):
    """The guard on `_CEILING` drifting from the weights it mirrors.

    Each entry is the ceiling written into that driver's satisfied branch. If a
    weight is raised in one place and not the other, the counterfactual quietly
    understates what a clinician would gain — so it is asserted rather than
    trusted to stay in step.
    """
    for note in (THIN, COMPLETE):
        for driver in _assess(parser, note).drivers:
            ceiling = _CEILING.get(driver.key)
            if ceiling is None or not driver.satisfied:
                continue
            assert driver.delta <= ceiling + 1e-9, (
                f"{driver.key} awarded {driver.delta}, above its ceiling {ceiling}"
            )


def test_nothing_documentation_cannot_fix_is_priced(parser):
    """Red flags are a bonus for what the patient presents with, and an
    exclusion is a denial no amount of typing overturns. Offering either a
    "document this for +N" would be advice to falsify a note."""
    assert "red_flags" not in _CEILING
    assert "payer_exclusion" not in _CEILING


def test_the_score_is_not_described_as_a_probability(parser):
    """Clinchec has never seen a submitted request's outcome.

    "Approval likelihood 92%" asserted a calibration nobody measured. The
    number is how completely the note documents what the criteria ask for.
    """
    assessment = _assess(parser, COMPLETE)
    assert "likelihood" not in assessment.rationale.lower()
    assert assessment.rationale.startswith("Documentation score")


def test_gaps_carry_their_driver_and_are_ranked(parser):
    """Each gap names the driver it belongs to, so the price is never inferred.

    Ranked by worth, so the first entry is the one to write if the clinician
    only writes one — the engine records gaps in evaluation order, which is a
    different thing entirely.
    """
    assessment = _assess(parser, THIN)
    assert assessment.gaps

    priced = [g.potential_delta for g in assessment.gaps if g.potential_delta]
    assert priced == sorted(priced, reverse=True), "gaps are not ranked by worth"

    for gap in assessment.gaps:
        assert gap.driver_key, gap.text
        assert gap.driver_key in _CEILING or gap.potential_delta is None


def test_a_bonus_driver_absent_is_priced_at_its_ceiling(parser):
    """Functional impairment awards up to 5 and penalises nothing when absent.

    No driver exists to carry a penalty, so an earlier version left the gap
    unpriced — understating it as zero when the swing is exactly the ceiling.
    """
    assessment = _assess(parser, THIN)
    impairment = next(
        g for g in assessment.gaps if g.driver_key == "functional_impairment"
    )
    assert impairment.potential_delta == _CEILING["functional_impairment"]
    assert not any(d.key == "functional_impairment" for d in assessment.drivers)


def test_gaps_and_missing_elements_carry_the_same_text(parser):
    """`missing_elements` is the flat projection, kept for the justification
    drafter. Same content, so a caller reading either sees the same gaps."""
    assessment = _assess(parser, THIN)
    assert sorted(g.text for g in assessment.gaps) == sorted(assessment.missing_elements)


def test_gaps_are_never_paired_by_position(parser):
    """The join this replaced.

    Functional impairment records a gap from a branch with no unmet driver, so
    the nth gap and the nth unmet driver are different things. Asserted here so
    nobody reintroduces the shortcut after seeing the two lists line up on a
    note where they happen to.
    """
    assessment = _assess(parser, THIN)
    unmet = [d for d in assessment.drivers if not d.satisfied]
    by_position = [d.key for d in unmet]
    by_key = [g.driver_key for g in assessment.gaps]
    assert by_key != by_position[: len(by_key)]

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

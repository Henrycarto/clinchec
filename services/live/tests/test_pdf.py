"""PDF section slicing and code-table reading.

The section slicer is the part with a trap in it. Every UnitedHealthcare policy
opens with a table of contents that repeats each heading verbatim, so a
substring search finds the contents entry first and returns a list of page
numbers as the criteria.
"""

from __future__ import annotations

from app.utils.pdf import code_table, extract_text, section

UNTIL = ["Definitions", "Applicable Codes", "Clinical Evidence"]

#: Shaped like the real thing: a table of contents with dot leaders, then the
#: sections themselves.
POLICY = """
Surgery of the Knee Page 1 of 10

Table of Contents Page
Application ......................................................... 1
Coverage Rationale .................................................. 1
Definitions ......................................................... 3
Applicable Codes .................................................... 4
Clinical Evidence ................................................... 6

Application

This Medical Policy applies to UnitedHealthcare Commercial benefit plans.

Coverage Rationale

Surgery of the knee is proven and medically necessary in certain circumstances.
For medical necessity clinical coverage criteria, refer to the InterQual CP.

Definitions

Disabling Pain: WOMAC pain domain of > 40.

Applicable Codes

CPT Code Description
0737T Xenograft implantation into the articular surface
27447 Arthroplasty, knee, condyle and plateau
E0601 Continuous positive airway pressure device
Not a code line at all
99 Too short to be a code

Clinical Evidence

Sixteen studies were reviewed.
"""


def test_section_skips_the_table_of_contents():
    body = section(POLICY, "Coverage Rationale", UNTIL)
    assert body.startswith("Surgery of the knee is proven")
    assert "....." not in body


def test_section_stops_at_the_next_named_heading():
    body = section(POLICY, "Coverage Rationale", UNTIL)
    assert "WOMAC" not in body
    assert "Sixteen studies" not in body


def test_missing_heading_returns_nothing_not_everything():
    """Falling back to the whole document is how a literature review becomes
    criteria. The caller treats "" as "skip this document", which is right."""
    assert section(POLICY, "Coverage Criteria", UNTIL) == ""


def test_unterminated_section_runs_to_the_end():
    body = section(POLICY, "Clinical Evidence", ["References"])
    assert body == "Sixteen studies were reviewed."


def test_code_table_reads_cpt_hcpcs_and_category_iii():
    codes = code_table(section(POLICY, "Applicable Codes", ["Clinical Evidence"]))
    assert set(codes) == {"0737T", "27447", "E0601"}
    assert codes["27447"].startswith("Arthroplasty, knee")


def test_unreadable_pdf_returns_empty_rather_than_raising():
    """One corrupt policy document must not sink a payer's whole crawl."""
    assert extract_text(b"not a pdf at all") == ""

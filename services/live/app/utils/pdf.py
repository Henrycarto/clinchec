"""PDF text extraction and section slicing.

Not every payer publishes criteria as HTML. UnitedHealthcare publishes each
medical policy as a PDF under `/content/dam/provider/docs/public/policies/`, and
the criteria live in a named section — "Coverage Rationale" — bounded by the next
named section. So the two things an adapter needs from a PDF are its text and a
way to cut a named section out of it.

The section slicer is line-anchored on purpose. Every UHC policy opens with a
table of contents that repeats each heading verbatim:

    Coverage Rationale ............................................ 1

A substring search finds the table of contents first and returns a page-number
listing as the criteria. Requiring the heading to be alone on its line skips the
contents entry, because the dot leaders and page number are on the same line.
"""

from __future__ import annotations

import io
import logging
import re

logger = logging.getLogger(__name__)

#: Payer policies run to ~60 pages. Beyond that we are reading something else —
#: an archive bundle or an update log — and extraction is wasted work.
DEFAULT_MAX_PAGES = 120


def extract_text(data: bytes, *, max_pages: int = DEFAULT_MAX_PAGES) -> str:
    """Text of a PDF, or "" if it cannot be read.

    Returns rather than raises: one unparseable policy document must not sink a
    crawl, and the caller already treats an empty parse as "skip this document"
    rather than as good criteria.
    """
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is declared
        logger.error("pdf: pypdf is not installed; PDF policies cannot be read")
        return ""

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[:max_pages]
        text = "\n".join(page.extract_text() or "" for page in pages)
    except Exception as exc:  # noqa: BLE001 — pypdf raises a wide variety
        logger.warning("pdf: extraction failed (%s)", exc)
        return ""

    # Collapse runs of spaces but keep line breaks: the section slicer anchors
    # on them, and so does any list-item detection downstream.
    return re.sub(r"[ \t\xa0]+", " ", text)


def _heading_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"^[ \t]*{re.escape(name)}[ \t]*:?[ \t]*$", re.MULTILINE)


def section(text: str, name: str, until: list[str]) -> str:
    """The body of section `name`, ending at whichever of `until` comes first.

    An unmatched heading returns "" rather than the whole document. Falling back
    to the full text would hand a criteria parser the policy's literature review
    and revision history, which is how a bounded, checkable extraction turns into
    a plausible-looking one.
    """
    start = _heading_re(name).search(text)
    if start is None:
        return ""

    body = text[start.end():]
    ends = [m.start() for m in (_heading_re(u).search(body) for u in until) if m]
    return body[: min(ends)].strip() if ends else body.strip()


#: A CPT code is five digits, or four digits plus a trailing letter for Category
#: II/III codes (0737T). HCPCS Level II is a letter then four digits (E0601).
_CODE_RE = re.compile(r"^(?P<code>\d{4}[0-9A-Z]|[A-Z]\d{4})[ \t]+(?P<label>\S.*)$")


def code_table(text: str) -> dict[str, str]:
    """Codes and their descriptions from a payer's applicable-codes section.

    UHC lists them one per line as `<code> <description>`, which is the payer's
    own statement of which codes a policy covers. Reading it is what keeps the
    CPT-to-policy mapping derived rather than assumed — the alternative is a
    hand-maintained keyword table, and a wrong entry there is invisible until it
    mis-scores a request.
    """
    codes: dict[str, str] = {}
    for line in text.splitlines():
        match = _CODE_RE.match(line.strip())
        if match:
            codes.setdefault(match.group("code"), match.group("label").strip())
    return codes

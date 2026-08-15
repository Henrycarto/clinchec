"""The TypeScript mirror must know every value the service can emit.

`packages/shared-types` parses every response through a Zod mirror of the
service's Pydantic model, which is what makes a shape mismatch fail loudly at
the boundary instead of propagating `undefined` into a component that renders a
clinical judgement. The cost is that the mirror has to be maintained, and a
missed enum value is invisible until a clinician hits the one code path that
produces it.

That happened: `CoverageStatus.CRITERIA_DELEGATED` was added to the service and
not to the Zod enum, so every UnitedHealthcare knee, hip and shoulder scan —
exactly the delegated cases — failed the parse and rendered "version mismatch"
instead of a result. Nothing caught it, because the service tests never go
through Zod and the web tests never call the service.

So the enums are compared here, where both files are readable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.schemas import ApprovalBand, CoverageStatus

MIRROR = (
    Path(__file__).resolve().parents[3] / "packages" / "shared-types" / "src" / "scan.ts"
)


def _zod_enum(name: str) -> set[str]:
    if not MIRROR.exists():  # pragma: no cover - shared types not checked out
        pytest.skip(f"shared-types mirror not present at {MIRROR}")
    source = MIRROR.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = z\.enum\(\[(.*?)\]\)", source, re.DOTALL)
    assert match, f"{name} not found in {MIRROR.name}"
    # Comments inside the array are stripped by taking only quoted members.
    return set(re.findall(r"'([a-z0-9_]+)'", match.group(1)))


def test_coverage_status_mirror_is_complete():
    """A value the service emits and the mirror omits fails the entire parse.

    Not just the field — Zod rejects the whole response, so a delegated rule
    took out the score, the codes and the evidence along with it.
    """
    assert _zod_enum("coverageStatusSchema") == {s.value for s in CoverageStatus}


def test_approval_band_mirror_is_complete():
    assert _zod_enum("approvalBandSchema") == {b.value for b in ApprovalBand}

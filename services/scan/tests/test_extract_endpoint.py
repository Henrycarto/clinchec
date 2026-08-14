"""End-to-end tests for POST /extract, including the response envelope,
code resolution and the approval-confidence bands.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.scoring import BASELINE_SCORE

# A strong request: on-label indication, duration met, conservative care and
# prior imaging documented, laterality explicit.
NOTE_STRONG = """\
S: 62 y/o female presents with right knee pain for 14 months. She has completed
12 weeks of physical therapy, a corticosteroid injection, and activity modification
without lasting relief. She is unable to climb stairs and reports difficulty walking.

O: Weight-bearing x-ray shows bone-on-bone medial compartment narrowing. Antalgic gait.

A: Right knee osteoarthritis, tricompartmental.

P: Proceed with total knee arthroplasty, right.
"""

# A weak request: no duration, no conservative care, no imaging, no laterality.
NOTE_WEAK = """\
S: Patient with knee pain.

A: Knee pain.

P: Total knee arthroplasty.
"""

# Mid-strength: indication and duration present, conservative care missing.
NOTE_MIDDLING = """\
S: 47 y/o male with low back pain for 5 months radiating into the right leg.
Lumbar x-ray obtained. Unable to work as a warehouse loader.

A: Lumbar radiculopathy.

P: Order MRI lumbar spine.
"""


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


# --- Envelope --------------------------------------------------------------

def test_response_uses_the_standard_envelope(client):
    response = client.post("/extract", json={"note": NOTE_STRONG})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"data", "error", "meta"}
    assert body["error"] is None
    assert body["meta"]["service"] == "clinchec-scan"
    assert body["meta"]["request_id"]
    assert body["meta"]["duration_ms"] is not None


def test_errors_also_use_the_envelope(client):
    response = client.post("/extract", json={"note": "too short"})
    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "note_too_short"
    assert body["error"]["details"]["minimum_chars"] == 20


def test_schema_violation_is_enveloped(client):
    response = client.post("/extract", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["data"] is None


def test_blank_note_is_rejected(client):
    response = client.post("/extract", json={"note": "   \n  "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --- Extraction ------------------------------------------------------------

def test_extracts_demographics_complaint_and_duration(client):
    data = client.post("/extract", json={"note": NOTE_STRONG}).json()["data"]
    extraction = data["extraction"]

    assert extraction["demographics"]["age"] == 62
    assert extraction["demographics"]["sex"] == "female"
    assert "right knee pain" in (extraction["chief_complaint"] or "")
    assert extraction["duration"]["value"] == 14
    assert extraction["duration"]["unit"] == "months"


def test_resolves_icd10_and_cpt_codes(client):
    data = client.post("/extract", json={"note": NOTE_STRONG}).json()["data"]
    extraction = data["extraction"]

    diagnosis_codes = {d["code"] for d in extraction["diagnoses"]}
    procedure_codes = {p["code"] for p in extraction["procedures"]}

    # Right-sided knee OA must resolve to the right-sided code, not the
    # unspecified one.
    assert "M17.11" in diagnosis_codes
    assert "27447" in procedure_codes


def test_laterality_is_captured(client):
    data = client.post("/extract", json={"note": NOTE_STRONG}).json()["data"]
    knee_oa = [d for d in data["extraction"]["diagnoses"] if d["code"] == "M17.11"]
    assert knee_oa and knee_oa[0]["laterality"] == "right"


def test_note_digest_is_returned_and_stable(client):
    first = client.post("/extract", json={"note": NOTE_STRONG}).json()["data"]
    second = client.post("/extract", json={"note": NOTE_STRONG}).json()["data"]
    assert first["note_sha256"] == second["note_sha256"]
    assert len(first["note_sha256"]) == 64
    assert first["scan_id"] != second["scan_id"]


# --- Approval scoring ------------------------------------------------------

def test_well_documented_request_scores_green(client):
    approval = client.post("/extract", json={"note": NOTE_STRONG}).json()["data"]["approval"]
    assert approval["band"] == "green"
    assert approval["score"] >= 0.80
    assert approval["basis"] == "rule_engine"


def test_undocumented_request_scores_red(client):
    approval = client.post("/extract", json={"note": NOTE_WEAK}).json()["data"]["approval"]
    assert approval["band"] == "red"
    assert approval["score"] < 0.50
    assert approval["missing_elements"]


def test_partial_documentation_scores_amber(client):
    approval = client.post("/extract", json={"note": NOTE_MIDDLING}).json()["data"]["approval"]
    assert approval["band"] == "amber"
    assert 0.50 <= approval["score"] < 0.80


def test_score_is_explainable(client):
    approval = client.post("/extract", json={"note": NOTE_WEAK}).json()["data"]["approval"]
    keys = {d["key"] for d in approval["drivers"]}
    assert {"procedure_identified", "indication", "duration", "conservative_care"} <= keys
    for driver in approval["drivers"]:
        assert driver["detail"]
        assert isinstance(driver["satisfied"], bool)


def test_drivers_sum_matches_the_reported_score(client):
    approval = client.post("/extract", json={"note": NOTE_STRONG}).json()["data"]["approval"]
    total = BASELINE_SCORE + sum(d["delta"] for d in approval["drivers"])
    assert approval["score"] == pytest.approx(min(max(total, 0.0), 1.0), abs=1e-3)


def test_missing_conservative_care_is_called_out(client):
    approval = client.post("/extract", json={"note": NOTE_MIDDLING}).json()["data"]["approval"]
    conservative = next(d for d in approval["drivers"] if d["key"] == "conservative_care")
    assert conservative["satisfied"] is False
    assert conservative["delta"] < 0


# --- Requested CPT override -------------------------------------------------

def test_requested_cpt_overrides_the_inferred_procedure(client):
    response = client.post(
        "/extract",
        json={"note": NOTE_MIDDLING, "requested_cpt": "62323"},
    )
    data = response.json()["data"]
    procedure_driver = next(
        d for d in data["approval"]["drivers"] if d["key"] == "procedure_identified"
    )
    assert "62323" in procedure_driver["detail"]


def test_unknown_requested_cpt_is_rejected(client):
    response = client.post(
        "/extract",
        json={"note": NOTE_MIDDLING, "requested_cpt": "99999"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_cpt"


# --- Justification ----------------------------------------------------------

def test_justification_is_omitted_by_default(client):
    data = client.post("/extract", json={"note": NOTE_STRONG}).json()["data"]
    assert data["extraction"]["justification"] is None


def test_template_justification_is_grounded_in_the_note(client):
    data = client.post(
        "/extract",
        json={"note": NOTE_STRONG, "draft_justification": True},
    ).json()["data"]

    justification = data["extraction"]["justification"]
    assert justification["generated_by"] == "template"
    assert "62" in justification["text"]
    assert "physical therapy" in justification["text"]
    assert "27447" in justification["text"]
    assert justification["citations"]


# --- Health -----------------------------------------------------------------

def test_health_reports_the_loaded_pipeline(client):
    body = client.get("/health").json()
    assert body["data"]["status"] == "ok"
    assert body["data"]["pipeline_loaded"] is True
    assert body["error"] is None

"""Form resolution, field mapping and submission-gate tests."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mappers import form_registry
from app.mappers.field_mapper import TRANSFORMS, map_payload, resolve_path
from app.schemas import (
    ClinicalPayload,
    MappingConfidence,
    PaPayload,
    PatientPayload,
    ProviderPayload,
)

# 1234567893 satisfies the NPI Luhn check; 1234567890 does not.
VALID_NPI = "1234567893"
INVALID_NPI = "1234567890"


def _payload(**overrides) -> PaPayload:
    base = PaPayload(
        payer_slug="aetna",
        patient=PatientPayload(
            first_name="Dana",
            last_name="Whitfield",
            date_of_birth=date(1962, 4, 18),
            sex="female",
            member_id="W123456789",
            group_number="GRP-4412",
            phone="312-555-0142",
            state="il",
        ),
        provider=ProviderPayload(
            name="Dr. Amara Osei",
            npi=VALID_NPI,
            tax_id="36-1234567",
            specialty="Orthopaedic Surgery",
            phone="(312) 555-0100",
            fax="3125550101",
            facility_name="Lakeshore Orthopaedic Center",
        ),
        clinical=ClinicalPayload(
            primary_icd10="M17.11",
            icd10_codes=["M17.11", "M25.561"],
            diagnosis_description="Unilateral primary osteoarthritis, right knee",
            cpt_code="27447",
            procedure_description="Total knee arthroplasty",
            laterality="right",
            symptom_duration_weeks=60.8,
            conservative_care=["physical therapy", "corticosteroid injection"],
            prior_imaging=["weight-bearing x-ray"],
            functional_impairment=["unable to climb stairs"],
            clinical_justification="This 62-year-old patient has failed conservative care.",
            requested_start_date=date(2026, 9, 1),
            place_of_service="Outpatient hospital",
        ),
    )
    return base.model_copy(update=overrides)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


# --- Form resolution --------------------------------------------------------

def test_cpt_specific_form_beats_the_payer_default():
    form = form_registry.resolve_form("aetna", "27447")
    assert form.form_key == "aetna-msk-pa-v3"


def test_different_cpt_selects_a_different_aetna_form():
    form = form_registry.resolve_form("aetna", "95810")
    assert form.form_key == "aetna-sleep-dme-v1"


def test_payer_default_is_used_for_an_unlisted_cpt():
    form = form_registry.resolve_form("bcbs", "99999")
    assert form.form_key == "bcbs-universal-pa-v1"


def test_unknown_payer_falls_back_to_the_universal_form():
    form = form_registry.resolve_form("cigna", "27447")
    assert form.form_key == "universal-pa-v2"


def test_every_form_has_unique_field_keys():
    for form in form_registry.ALL_FORMS:
        keys = [field.key for field in form.fields]
        assert len(keys) == len(set(keys)), f"{form.form_key} has duplicate field keys"


def test_every_declared_transform_exists():
    for form in form_registry.ALL_FORMS:
        for field in form.fields:
            if field.transform:
                assert field.transform in TRANSFORMS, (
                    f"{form.form_key}.{field.key} references unknown transform "
                    f"{field.transform!r}"
                )


def test_every_source_path_resolves_on_the_payload_model():
    payload = _payload()
    for form in form_registry.ALL_FORMS:
        for field in form.fields:
            if field.source:
                # resolve_path returning None is fine; raising is not.
                resolve_path(payload, field.source)


# --- Mapping ----------------------------------------------------------------

def test_populates_patient_and_provider_fields():
    form = form_registry.resolve_form("aetna", "27447")
    result = map_payload(form, _payload())

    assert result.values["patient_last_name"] == "Whitfield"
    assert result.values["patient_dob"] == "04/18/1962"
    assert result.values["patient_sex"] == "F"
    assert result.values["provider_npi"] == VALID_NPI
    assert result.values["provider_phone"] == "(312) 555-0100"
    assert result.values["provider_fax"] == "(312) 555-0101"


def test_normalises_clinical_fields():
    form = form_registry.resolve_form("aetna", "27447")
    result = map_payload(form, _payload())

    assert result.values["requested_cpt"] == "27447"
    assert result.values["laterality"] == "Right"
    assert result.values["urgency"] == "Routine"
    # 60.8 weeks reads as years, not "60 weeks".
    assert "year" in result.values["symptom_duration"]
    assert result.values["conservative_therapy"] == (
        "physical therapy; corticosteroid injection"
    )
    # The primary code has its own field and must not repeat in the secondary list.
    assert result.values["secondary_diagnosis_codes"] == "M25.561"


def test_attestation_checkboxes_are_derived_from_evidence():
    form = form_registry.resolve_form("aetna", "27447")
    result = map_payload(form, _payload())
    assert result.values["failed_conservative_care_confirmation"] is True
    assert result.values["radiographic_confirmation"] is True


def test_attestation_is_false_when_evidence_is_absent():
    form = form_registry.resolve_form("aetna", "27447")
    payload = _payload()
    payload.clinical.conservative_care = []
    result = map_payload(form, payload)

    field = next(f for f in result.fields if f.key == "failed_conservative_care_confirmation")
    assert field.value is False
    assert "failed_conservative_care_confirmation" in result.missing_required


def test_uhc_imaging_form_requires_prior_radiographs():
    form = form_registry.resolve_form("uhc", "72148")
    assert form.form_key == "uhc-advanced-imaging-v2"

    payload = _payload(payer_slug="uhc")
    payload.clinical.cpt_code = "72148"
    payload.clinical.prior_imaging = ["lumbar x-ray"]
    result = map_payload(form, payload)
    assert result.values["prior_xray_completed"] is True

    payload.clinical.prior_imaging = []
    blocked = map_payload(form, payload)
    assert "prior_xray_completed" in blocked.missing_required


# --- Provenance -------------------------------------------------------------

def test_structured_fields_are_marked_exact():
    form = form_registry.resolve_form("aetna", "27447")
    result = map_payload(form, _payload())
    member_id = next(f for f in result.fields if f.key == "member_id")
    assert member_id.confidence is MappingConfidence.EXACT


def test_nlp_derived_fields_are_flagged_for_review():
    form = form_registry.resolve_form("aetna", "27447")
    result = map_payload(form, _payload())

    justification = next(f for f in result.fields if f.key == "clinical_justification")
    assert justification.confidence is MappingConfidence.INFERRED
    assert "clinical_justification" in result.needs_review
    assert justification.note


def test_reformatted_fields_are_marked_derived():
    form = form_registry.resolve_form("aetna", "27447")
    result = map_payload(form, _payload())
    dob = next(f for f in result.fields if f.key == "patient_dob")
    assert dob.confidence is MappingConfidence.DERIVED


def test_fields_with_no_source_are_reported_missing_not_guessed():
    form = form_registry.get_form("aetna-sleep-dme-v1")
    payload = _payload()
    payload.clinical.cpt_code = "95810"
    result = map_payload(form, payload)

    bmi = next(f for f in result.fields if f.key == "bmi")
    assert bmi.value is None
    assert bmi.confidence is MappingConfidence.MISSING
    assert bmi.note


# --- Transform edge cases ---------------------------------------------------

def test_invalid_npi_is_dropped_rather_than_passed_through():
    form = form_registry.resolve_form("aetna", "27447")
    payload = _payload()
    payload.provider.npi = INVALID_NPI
    result = map_payload(form, payload)

    assert "provider_npi" not in result.values
    assert "provider_npi" in result.missing_required


def test_malformed_phone_is_dropped():
    form = form_registry.resolve_form("aetna", "27447")
    payload = _payload()
    payload.provider.phone = "555-01"
    result = map_payload(form, payload)
    assert "provider_phone" in result.missing_required


def test_justification_is_truncated_at_a_sentence_boundary():
    form = form_registry.resolve_form("aetna", "27447")
    payload = _payload()
    payload.clinical.clinical_justification = ("Sentence one is here. " * 200).strip()
    result = map_payload(form, payload)

    text = result.values["clinical_justification"]
    assert len(text) <= 1500
    assert text.endswith(".")


def test_clinician_overrides_win_and_are_not_flagged():
    form = form_registry.resolve_form("aetna", "27447")
    result = map_payload(form, _payload(), overrides={"clinical_justification": "Edited by MD."})

    field = next(f for f in result.fields if f.key == "clinical_justification")
    assert field.value == "Edited by MD."
    assert field.confidence is MappingConfidence.EXACT
    assert "clinical_justification" not in result.needs_review


def test_completeness_is_reported():
    form = form_registry.resolve_form("aetna", "27447")
    result = map_payload(form, _payload())
    assert 0.0 < result.completeness <= 1.0
    assert result.ready_to_submit is True


# --- Endpoints --------------------------------------------------------------

def test_populate_returns_the_envelope(client):
    response = client.post("/populate", json={"payload": _payload().model_dump(mode="json")})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"data", "error", "meta"}
    assert body["error"] is None
    assert body["data"]["form_key"] == "aetna-msk-pa-v3"
    assert body["data"]["values"]["patient_sex"] == "F"


def test_populate_accepts_an_explicit_form_key(client):
    response = client.post(
        "/populate",
        json={
            "payload": _payload().model_dump(mode="json"),
            "form_key": "universal-pa-v2",
        },
    )
    assert response.json()["data"]["form_key"] == "universal-pa-v2"


def test_unknown_form_key_is_rejected(client):
    response = client.post(
        "/populate",
        json={"payload": _payload().model_dump(mode="json"), "form_key": "nope-v1"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_form"


def test_incomplete_submission_is_blocked(client):
    payload = _payload()
    payload.provider.npi = None
    response = client.post("/submit", json={"payload": payload.model_dump(mode="json")})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "incomplete_form"
    assert "provider_npi" in error["details"]["missing_required"]


def test_incomplete_submission_can_be_forced(client):
    payload = _payload()
    payload.provider.npi = None
    response = client.post(
        "/submit",
        json={"payload": payload.model_dump(mode="json"), "allow_incomplete": True},
    )
    assert response.status_code == 200


def test_submission_without_credentials_exports_instead_of_transmitting(client):
    response = client.post("/submit", json={"payload": _payload().model_dump(mode="json")})
    data = response.json()["data"]

    assert data["status"] == "exported"
    assert data["export_url"]
    assert data["submission_ref"] is None
    # The caller must be able to tell nothing was actually sent to the payer.
    assert response.json()["meta"]["extra"]["transmitted"] is False


def test_overrides_are_applied_on_submit(client):
    response = client.post(
        "/submit",
        json={
            "payload": _payload().model_dump(mode="json"),
            "overrides": {"clinical_justification": "Reviewed and edited by Dr. Osei."},
        },
    )
    values = response.json()["data"]["mapping"]["values"]
    assert values["clinical_justification"] == "Reviewed and edited by Dr. Osei."


def test_forms_listing(client):
    body = client.get("/forms").json()
    assert body["meta"]["extra"]["count"] == len(form_registry.ALL_FORMS)


def test_health(client):
    body = client.get("/health").json()
    assert body["data"]["status"] == "ok"
    assert body["data"]["registered_forms"] > 0

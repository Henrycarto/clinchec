"""Wire contract for Clinchec Forms."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FieldType(StrEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    DATE = "date"
    NUMBER = "number"
    SELECT = "select"
    CHECKBOX = "checkbox"
    PHONE = "phone"
    NPI = "npi"


class SubmissionChannel(StrEnum):
    PORTAL = "portal"      # authenticated payer web portal, RPA-driven
    FAX = "fax"            # rendered PDF to an e-fax gateway
    X12_278 = "x12_278"    # EDI 278 request/response
    FHIR_CRD = "fhir_crd"  # Da Vinci Coverage Requirements Discovery
    EXPORT = "export"      # download only, clinician submits manually


class SubmissionStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    SUBMITTED = "submitted"
    EXPORTED = "exported"
    BLOCKED = "blocked"


class MappingConfidence(StrEnum):
    EXACT = "exact"        # copied verbatim from a structured field
    DERIVED = "derived"    # computed or reformatted from structured data
    INFERRED = "inferred"  # taken from NLP extraction; clinician should verify
    MISSING = "missing"    # no source available


# ---------------------------------------------------------------------------
# Form definitions
# ---------------------------------------------------------------------------

class FormField(BaseModel):
    key: str
    label: str
    type: FieldType = FieldType.TEXT
    required: bool = False
    #: Dotted path into the canonical PA payload, e.g. "patient.date_of_birth".
    source: str | None = None
    #: Named transform applied to the resolved value.
    transform: str | None = None
    options: list[str] = Field(default_factory=list)
    max_length: int | None = None
    help_text: str | None = None
    #: Section heading this field appears under on the payer's form.
    section: str = "General"


class FormDefinition(BaseModel):
    form_key: str
    payer_slug: str
    display_name: str
    version: str
    cpt_codes: list[str] = Field(default_factory=list)
    channel: SubmissionChannel = SubmissionChannel.EXPORT
    fields: list[FormField] = Field(default_factory=list)
    notes: str | None = None

    def field_map(self) -> dict[str, FormField]:
        return {field.key: field for field in self.fields}


# ---------------------------------------------------------------------------
# Canonical PA payload — what the mapper reads from
# ---------------------------------------------------------------------------

class PatientPayload(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    sex: Literal["male", "female", "other", "unknown"] = "unknown"
    member_id: str | None = None
    group_number: str | None = None
    phone: str | None = None
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None


class ProviderPayload(BaseModel):
    name: str | None = None
    npi: str | None = None
    tax_id: str | None = None
    specialty: str | None = None
    phone: str | None = None
    fax: str | None = None
    facility_name: str | None = None


class ClinicalPayload(BaseModel):
    """The Scan extraction, flattened to what a PA form asks for."""

    model_config = ConfigDict(protected_namespaces=())

    primary_icd10: str | None = None
    icd10_codes: list[str] = Field(default_factory=list)
    diagnosis_description: str | None = None
    cpt_code: str | None = None
    procedure_description: str | None = None
    laterality: Literal["left", "right", "bilateral"] | None = None
    symptom_duration_weeks: float | None = None
    conservative_care: list[str] = Field(default_factory=list)
    prior_imaging: list[str] = Field(default_factory=list)
    functional_impairment: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    clinical_justification: str | None = None
    requested_start_date: date | None = None
    units_requested: int = 1
    place_of_service: str | None = None
    urgency: Literal["routine", "urgent"] = "routine"


class PaPayload(BaseModel):
    """Everything the mapper can draw on to fill a form."""

    payer_slug: str
    patient: PatientPayload = Field(default_factory=PatientPayload)
    provider: ProviderPayload = Field(default_factory=ProviderPayload)
    clinical: ClinicalPayload = Field(default_factory=ClinicalPayload)
    scan_id: str | None = None


# ---------------------------------------------------------------------------
# Mapping results
# ---------------------------------------------------------------------------

class MappedField(BaseModel):
    key: str
    label: str
    type: FieldType
    section: str
    required: bool
    value: Any | None = None
    confidence: MappingConfidence = MappingConfidence.MISSING
    source: str | None = None
    note: str | None = None


class MappingResult(BaseModel):
    form_key: str
    payer_slug: str
    display_name: str
    channel: SubmissionChannel
    fields: list[MappedField] = Field(default_factory=list)
    values: dict[str, Any] = Field(default_factory=dict)
    missing_required: list[str] = Field(default_factory=list)
    needs_review: list[str] = Field(default_factory=list)
    completeness: float = Field(default=0.0, ge=0.0, le=1.0)
    ready_to_submit: bool = False


# ---------------------------------------------------------------------------
# Requests / responses
# ---------------------------------------------------------------------------

class PopulateRequest(BaseModel):
    payload: PaPayload
    form_key: str | None = Field(
        default=None,
        description="Explicit form to fill. Defaults to the best match for the "
        "payer and CPT code.",
    )


class SubmitRequest(BaseModel):
    payload: PaPayload
    form_key: str | None = None
    #: Clinician edits applied on top of the auto-populated values.
    overrides: dict[str, Any] = Field(default_factory=dict)
    #: When false, a request with missing required fields is rejected outright.
    allow_incomplete: bool = False
    channel: SubmissionChannel | None = None


class SubmissionResult(BaseModel):
    pa_request_id: str
    form_key: str
    payer_slug: str
    status: SubmissionStatus
    channel: SubmissionChannel
    mapping: MappingResult
    submission_ref: str | None = None
    export_url: str | None = None
    message: str


class HealthResult(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    registered_forms: int
    payers: list[str]
    live_service_reachable: bool

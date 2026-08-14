"""Wire contract for Clinchec Scan.

These models are mirrored one-for-one by the Zod schemas in
`packages/shared-types`, which the Next.js app validates responses against.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SoapSection(StrEnum):
    SUBJECTIVE = "subjective"
    OBJECTIVE = "objective"
    ASSESSMENT = "assessment"
    PLAN = "plan"
    UNSECTIONED = "unsectioned"


class EntityLabel(StrEnum):
    DIAGNOSIS = "diagnosis"
    PROCEDURE = "procedure"
    CONSERVATIVE_CARE = "conservative_care"
    RED_FLAG = "red_flag"
    IMAGING_EVIDENCE = "imaging_evidence"
    FUNCTIONAL_IMPAIRMENT = "functional_impairment"
    ANATOMY = "anatomy"
    MEDICATION = "medication"


class ApprovalBand(StrEnum):
    GREEN = "green"   # >= 0.80
    AMBER = "amber"   # 0.50 – 0.79
    RED = "red"       # < 0.50


class CoverageStatus(StrEnum):
    """How confidently the payer's own criteria answer this request.

    Separate from the approval band on purpose. The band stays green/amber/red
    so the UI contract is unchanged, while this records what the score actually
    rests on — the difference between "we evaluated the payer's criteria" and
    "the payer publishes nothing about this indication" is invisible in a
    number, and it changes what the clinician should do next.
    """

    #: A payer clause matched and its criteria were evaluated.
    ADJUDICATED = "adjudicated"
    #: The payer explicitly excludes this indication. Documentation cannot fix it.
    EXCLUDED = "excluded"
    #: The payer publishes clauses, none covering this indication. Likely to go
    #: to manual review rather than auto-adjudication.
    INDICATION_NOT_ADDRESSED = "indication_not_addressed"
    #: No synced criteria for this payer and procedure at all. Scored against
    #: the national baseline, which is a guess and must be labelled as one.
    NO_CRITERIA_AVAILABLE = "no_criteria_available"


class Sex(StrEnum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Extraction primitives
# ---------------------------------------------------------------------------

class TextSpan(BaseModel):
    """A character-offset span into the original note.

    Offsets are relative to the raw note the caller submitted, so the frontend
    can highlight in place without re-tokenising.
    """

    text: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    section: SoapSection = SoapSection.UNSECTIONED


class ClinicalEntity(TextSpan):
    label: EntityLabel
    normalized: str = Field(description="Canonical lexicon term this span resolved to.")
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["lexicon", "ner", "regex"] = "lexicon"
    negated: bool = Field(
        default=False,
        description="Asserted as absent ('denies chest pain'). Never promoted to a code.",
    )
    uncertain: bool = Field(
        default=False,
        description="Hedged or hypothetical ('possible meniscal tear', 'rule out').",
    )


class Demographics(BaseModel):
    age: int | None = Field(default=None, ge=0, le=130)
    age_unit: Literal["years", "months", "days"] | None = None
    sex: Sex = Sex.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: TextSpan | None = None


class ConditionDuration(BaseModel):
    value: float = Field(gt=0)
    unit: Literal["days", "weeks", "months", "years"]
    normalized_weeks: float = Field(gt=0, description="Duration converted to weeks for scoring.")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: TextSpan


class CodeCandidate(BaseModel):
    code: str
    system: Literal["ICD-10-CM", "CPT"]
    description: str
    matched_text: str
    confidence: float = Field(ge=0.0, le=1.0)
    section: SoapSection = SoapSection.UNSECTIONED
    requires_laterality: bool = False
    laterality: Literal["left", "right", "bilateral"] | None = None


class ScoreDriver(BaseModel):
    """One explainable contribution to the approval confidence score."""

    key: str
    label: str
    delta: float = Field(description="Signed contribution to the 0–1 score.")
    detail: str
    satisfied: bool


class ApprovalAssessment(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    band: ApprovalBand
    rationale: str
    drivers: list[ScoreDriver] = Field(default_factory=list)
    missing_elements: list[str] = Field(default_factory=list)
    basis: Literal["rule_engine", "payer_rule", "ml_model"] = "rule_engine"
    payer_slug: str | None = None
    coverage_status: CoverageStatus = CoverageStatus.NO_CRITERIA_AVAILABLE
    #: The payer's own sentence when a clause decided the outcome. Quoted to the
    #: clinician verbatim, because an insurer's own wording is the strongest
    #: thing to put in an appeal.
    matched_indication: str | None = None
    payer_quote: str | None = None


class ClinicalJustification(BaseModel):
    text: str
    generated_by: Literal["template", "gpt-4o", "gpt-4o-mini"] = "template"
    citations: list[TextSpan] = Field(default_factory=list)


class SoapSectionText(BaseModel):
    section: SoapSection
    text: str
    start: int
    end: int


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------

class ExtractRequest(BaseModel):
    note: str = Field(min_length=1, description="Raw unstructured SOAP note text.")
    payer_slug: str | None = Field(
        default=None,
        description="Optional payer hint ('aetna' | 'bcbs' | 'uhc'). When supplied, "
        "scoring is evaluated against that payer's synced rules instead of the "
        "national baseline.",
    )
    requested_cpt: str | None = Field(
        default=None,
        description="Explicit CPT code the clinician intends to request. Overrides "
        "the procedure inferred from the note's Plan section.",
    )
    draft_justification: bool = Field(
        default=False,
        description="When true, drafts a payer-facing medical-necessity paragraph.",
    )

    @field_validator("note")
    @classmethod
    def _note_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note must contain non-whitespace text")
        return value

    @field_validator("payer_slug")
    @classmethod
    def _normalize_payer(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else None


class ExtractionResult(BaseModel):
    demographics: Demographics
    chief_complaint: str | None = None
    chief_complaint_span: TextSpan | None = None
    duration: ConditionDuration | None = None
    diagnoses: list[CodeCandidate] = Field(default_factory=list)
    procedures: list[CodeCandidate] = Field(default_factory=list)
    entities: list[ClinicalEntity] = Field(default_factory=list)
    sections: list[SoapSectionText] = Field(default_factory=list)
    justification: ClinicalJustification | None = None


class ScanResult(BaseModel):
    # `model_version` is a domain term here, not a Pydantic reserved prefix.
    model_config = ConfigDict(protected_namespaces=())

    scan_id: str
    extraction: ExtractionResult
    approval: ApprovalAssessment
    model_version: str
    note_char_count: int
    note_sha256: str


class HealthResult(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    spacy_model: str
    pipeline_loaded: bool
    justification_enabled: bool

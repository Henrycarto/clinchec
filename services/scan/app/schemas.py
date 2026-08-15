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
    #: The payer adjudicates this procedure against criteria it does not
    #: publish. UnitedHealthcare defers its surgical policies to InterQual:
    #: "Surgery of the knee is proven and medically necessary in certain
    #: circumstances. For medical necessity clinical coverage criteria, refer to
    #: the InterQual CP: Procedures." The circumstances are the part we cannot
    #: see, so the score rests on the national baseline and says so.
    CRITERIA_DELEGATED = "criteria_delegated"
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
    #: What satisfying this driver would be worth, for unmet drivers only.
    #:
    #: The swing, not the award: a driver sitting at -0.13 that would pay +0.13
    #: moves the score by 0.26. Arithmetic over the score's own weights, which
    #: is why it can be stated at all — it is not a probability. Clinchec has
    #: never observed a submitted request's outcome, so "raises approval odds
    #: by N%" would be invented where "adds 26 points" is checkable.
    potential_delta: float | None = Field(
        default=None,
        description="Points this would add to the score if documented. Absent "
        "on satisfied drivers and on anything documentation cannot fix.",
    )


class ScoreGap(BaseModel):
    """One documentation element the note is missing, and what it is worth.

    `missing_elements` carries the same text as a flat list and is kept for
    callers that only want the prose — the justification drafter is one. This
    adds the driver it belongs to, which is what lets a caller say how much
    closing it would move the score. The two cannot be paired by position:
    functional impairment records a gap from a branch with no unmet driver, so
    a positional join puts the wrong number against the wrong line.
    """

    text: str
    #: The `ScoreDriver.key` this gap belongs to.
    driver_key: str
    #: Points closing it would add, or None where nothing prices it — a gap
    #: recorded from a branch with no unmet driver, or one no documentation can
    #: close.
    potential_delta: float | None = None


class ApprovalAssessment(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    band: ApprovalBand
    rationale: str
    drivers: list[ScoreDriver] = Field(default_factory=list)
    missing_elements: list[str] = Field(default_factory=list)
    #: The same gaps, each tied to its driver and priced. Ordered by what
    #: closing it is worth, so the first entry is the one to write if the
    #: clinician only writes one.
    gaps: list[ScoreGap] = Field(default_factory=list)
    basis: Literal["rule_engine", "payer_rule", "ml_model"] = "rule_engine"
    payer_slug: str | None = None
    coverage_status: CoverageStatus = CoverageStatus.NO_CRITERIA_AVAILABLE
    #: The payer's own sentence when a clause decided the outcome. Quoted to the
    #: clinician verbatim, because an insurer's own wording is the strongest
    #: thing to put in an appeal.
    matched_indication: str | None = None
    payer_quote: str | None = None
    #: Payer clauses we could read but could not scope to a diagnosis code — the
    #: payer's restriction, surfaced as a question for the clinician rather than
    #: applied to the score. Marking a clause advisory and then not showing it
    #: would be strictly worse than never extracting it.
    advisories: list[str] = Field(default_factory=list)
    #: How the governing clause was selected: 'text' (the note's own language),
    #: 'icd10' (diagnosis codes), or 'none'. Recorded because the two mechanisms
    #: carry different confidence and a reviewer should know which decided.
    indication_match_method: Literal["text", "icd10", "none"] = "none"


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
    plan_type: str | None = Field(
        default=None,
        description="Optional plan type ('commercial' | 'medicare_advantage' | "
        "'medicaid' | 'exchange'). The same payer adjudicates the same CPT "
        "differently by line of business — UnitedHealthcare states its own "
        "criteria for a knee replacement under a commercial plan and routes the "
        "Medicare Advantage version to a CMS coverage determination — so "
        "omitting this scores against whichever rule the rules service returns "
        "by default.",
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

    @field_validator("payer_slug", "plan_type")
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

"""`POST /extract` — the endpoint the physician's Scan button calls.

Takes a raw SOAP note, returns the structured extraction plus a preliminary
approval-confidence score, wrapped in the standard `{ data, error, meta }`
envelope.

The spaCy pass is CPU-bound, so it runs in the threadpool rather than blocking
the event loop; everything else on the path is genuinely async.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.envelope import ApiError, Envelope, ok
from app.models import icd_extractor
from app.models.justification import get_drafter
from app.models.scoring import PayerRule, RuleClause, evaluate
from app.models.soap_parser import MODEL_VERSION, ParsedNote, get_parser
from app.schemas import (
    ExtractionResult,
    ExtractRequest,
    ScanResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scan"])

MIN_NOTE_CHARS = 20


@router.post(
    "/extract",
    response_model=Envelope[ScanResult],
    summary="Extract structured clinical data from a SOAP note",
    response_description="Structured extraction plus preliminary approval confidence.",
)
async def extract(
    payload: ExtractRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Envelope[ScanResult]:
    started = time.perf_counter()

    note = payload.note.strip()
    if len(note) < MIN_NOTE_CHARS:
        raise ApiError(
            "note_too_short",
            f"A SOAP note needs at least {MIN_NOTE_CHARS} characters to extract from; "
            f"received {len(note)}.",
            status_code=422,
            details={"received_chars": len(note), "minimum_chars": MIN_NOTE_CHARS},
        )

    truncated = len(note) > settings.max_note_chars
    if truncated:
        logger.warning(
            "Note exceeded max_note_chars (%d > %d); truncating",
            len(note),
            settings.max_note_chars,
        )
        note = note[: settings.max_note_chars]

    # --- 1. Parse -------------------------------------------------------
    parser = get_parser(settings.active_spacy_model)
    parsed: ParsedNote = await run_in_threadpool(parser.parse, note)

    # --- 2. Resolve codes ------------------------------------------------
    diagnoses, procedures = icd_extractor.extract_codes(parsed.entities, note)
    procedure = icd_extractor.primary_procedure(procedures, payload.requested_cpt)

    if payload.requested_cpt and procedure is None:
        raise ApiError(
            "unknown_cpt",
            f"CPT {payload.requested_cpt} is not in the Clinchec procedure registry.",
            status_code=422,
            details={"requested_cpt": payload.requested_cpt},
        )

    # --- 3. Score --------------------------------------------------------
    payer_rule = None
    if payload.payer_slug and procedure is not None:
        payer_rule = await _fetch_payer_rule(settings, payload.payer_slug, procedure.code)

    assessment = evaluate(parsed, diagnoses, procedure, payer_rule=payer_rule)

    # --- 4. Optionally draft the justification ---------------------------
    justification = None
    if payload.draft_justification:
        drafter = get_drafter(settings)
        justification = await drafter.draft(
            parsed,
            icd_extractor.primary_diagnosis(diagnoses),
            procedure,
            assessment,
        )

    extraction = ExtractionResult(
        demographics=parsed.demographics,
        chief_complaint=parsed.chief_complaint,
        chief_complaint_span=parsed.chief_complaint_span,
        duration=parsed.duration,
        diagnoses=diagnoses,
        procedures=procedures,
        entities=parsed.entities,
        sections=parsed.sections,
        justification=justification,
    )

    result = ScanResult(
        scan_id=str(uuid.uuid4()),
        extraction=extraction,
        approval=assessment,
        model_version=MODEL_VERSION,
        note_char_count=len(note),
        note_sha256=hashlib.sha256(note.encode("utf-8")).hexdigest(),
    )

    duration_ms = int((time.perf_counter() - started) * 1000)
    envelope = ok(
        result,
        request=request,
        entity_count=len(parsed.entities),
        payer_rule_applied=payer_rule is not None,
        note_truncated=truncated,
    )
    envelope.meta.duration_ms = duration_ms
    return envelope


async def _fetch_payer_rule(
    settings: Settings,
    payer_slug: str,
    cpt_code: str,
) -> PayerRule | None:
    """Ask Clinchec Live for this payer's current criteria.

    Live being unavailable must never fail a scan — the rule engine simply
    falls back to the national baseline and reports `basis="rule_engine"`, so
    the clinician still gets a score and knows which basis produced it.
    """
    url = f"{settings.live_service_url.rstrip('/')}/rules/{payer_slug}/{cpt_code}"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Payer rule lookup failed for %s/%s: %s", payer_slug, cpt_code, exc)
        return None

    data = body.get("data") if isinstance(body, dict) else None
    if not data:
        return None

    return PayerRule(
        payer_slug=payer_slug,
        cpt_code=cpt_code,
        requires_pa=bool(data.get("requires_pa", True)),
        required_duration_weeks=data.get("required_duration_weeks"),
        requires_conservative_care=(
            bool(data["required_conservative_care"])
            if data.get("required_conservative_care") is not None
            else None
        ),
        requires_prior_imaging=(
            bool(data["required_imaging"])
            if data.get("required_imaging") is not None
            else None
        ),
        supporting_icd10_prefixes=tuple(data.get("icd10_codes") or ()),
        clauses=tuple(
            RuleClause(
                polarity=clause.get("polarity", "covered"),
                indication_text=clause.get("indication_text", ""),
                indication_icd10_prefixes=tuple(
                    clause.get("indication_icd10_prefixes") or ()
                ),
                required_duration_weeks=clause.get("required_duration_weeks"),
                required_conservative_care=tuple(
                    clause.get("required_conservative_care") or ()
                ),
                required_imaging=tuple(clause.get("required_imaging") or ()),
                source_snippet=clause.get("source_snippet", ""),
                # Defaulting this to False would make an unscoped exclusion
                # selectable, and an exclusion with no ICD scope matches every
                # request — turning a clause meant to raise a question into one
                # that denies the entire procedure. Default to advisory when the
                # field is absent, so a stale Live version fails safe.
                advisory=bool(clause.get("advisory", True)),
            )
            for clause in (data.get("clauses") or [])
        ),
    )

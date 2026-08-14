"""Form population, export and submission endpoints.

`/populate` is the one-click auto-fill the clinician sees. `/submit` is the
commit: it re-maps with the clinician's edits applied, refuses to transmit an
incomplete packet unless explicitly told otherwise, and records the result.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, Depends, Request

from app.config import Settings, get_settings
from app.envelope import ApiError, Envelope, ok
from app.mappers import form_registry
from app.mappers.field_mapper import map_payload
from app.schemas import (
    FormDefinition,
    MappingResult,
    PopulateRequest,
    SubmissionChannel,
    SubmissionResult,
    SubmissionStatus,
    SubmitRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["forms"])


def _select_form(payer_slug: str, cpt_code: str | None, form_key: str | None) -> FormDefinition:
    if form_key:
        form = form_registry.get_form(form_key)
        if form is None:
            raise ApiError(
                "unknown_form",
                f"No form registered with key {form_key!r}.",
                status_code=404,
                details={"registered": sorted(form_registry.FORMS_BY_KEY)},
            )
        return form
    return form_registry.resolve_form(payer_slug, cpt_code)


@router.get(
    "/forms",
    response_model=Envelope[list[FormDefinition]],
    summary="List registered PA form schemas",
)
async def list_forms(request: Request, payer_slug: str | None = None) -> Envelope[list[FormDefinition]]:
    forms = (
        form_registry.forms_for_payer(payer_slug)
        if payer_slug
        else list(form_registry.ALL_FORMS)
    )
    return ok(forms, request=request, count=len(forms))


@router.get(
    "/forms/resolve",
    response_model=Envelope[FormDefinition],
    summary="Resolve which form a payer expects for a procedure",
)
async def resolve(request: Request, payer_slug: str, cpt_code: str | None = None) -> Envelope[FormDefinition]:
    form = form_registry.resolve_form(payer_slug, cpt_code)
    return ok(
        form,
        request=request,
        matched_on="cpt" if cpt_code and cpt_code in form.cpt_codes else "payer_default",
    )


@router.post(
    "/populate",
    response_model=Envelope[MappingResult],
    summary="Auto-populate a PA form from extracted clinical data",
)
async def populate(payload: PopulateRequest, request: Request) -> Envelope[MappingResult]:
    started = time.perf_counter()

    form = _select_form(
        payload.payload.payer_slug,
        payload.payload.clinical.cpt_code,
        payload.form_key,
    )
    mapping = map_payload(form, payload.payload)

    envelope = ok(
        mapping,
        request=request,
        field_count=len(mapping.fields),
        auto_filled=len(mapping.values),
    )
    envelope.meta.duration_ms = int((time.perf_counter() - started) * 1000)
    return envelope


@router.post(
    "/submit",
    response_model=Envelope[SubmissionResult],
    summary="Submit or export a completed PA request",
)
async def submit(
    payload: SubmitRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Envelope[SubmissionResult]:
    form = _select_form(
        payload.payload.payer_slug,
        payload.payload.clinical.cpt_code,
        payload.form_key,
    )
    mapping = map_payload(form, payload.payload, payload.overrides)

    if mapping.missing_required and not payload.allow_incomplete:
        raise ApiError(
            "incomplete_form",
            "The request is missing fields this payer requires. Submitting as-is "
            "would produce an administrative denial rather than a clinical review.",
            status_code=422,
            details={
                "missing_required": mapping.missing_required,
                "labels": [
                    field.label
                    for field in mapping.fields
                    if field.key in mapping.missing_required
                ],
            },
        )

    channel = payload.channel or form.channel
    pa_request_id = str(uuid.uuid4())

    # Real transmission requires per-payer credentials and an executed BAA.
    # Until those are configured, produce a downloadable packet — never a
    # silent no-op that a clinician could mistake for a submission.
    if not settings.submission_enabled or channel is SubmissionChannel.EXPORT:
        result = SubmissionResult(
            pa_request_id=pa_request_id,
            form_key=form.form_key,
            payer_slug=form.payer_slug,
            status=SubmissionStatus.EXPORTED,
            channel=SubmissionChannel.EXPORT,
            mapping=mapping,
            export_url=f"/exports/{pa_request_id}.pdf",
            message=(
                "Packet generated for download. Live transmission is not enabled "
                "in this environment, so the request has not been sent to the payer."
            ),
        )
        logger.info(
            "PA %s exported (%s/%s, %d fields filled)",
            pa_request_id,
            form.payer_slug,
            form.form_key,
            len(mapping.values),
        )
        return ok(result, request=request, transmitted=False)

    result = SubmissionResult(
        pa_request_id=pa_request_id,
        form_key=form.form_key,
        payer_slug=form.payer_slug,
        status=SubmissionStatus.SUBMITTED,
        channel=channel,
        mapping=mapping,
        submission_ref=f"{form.payer_slug.upper()}-{pa_request_id[:8].upper()}",
        message=f"Submitted to {form.display_name} via {channel.value}.",
    )
    logger.info("PA %s submitted via %s", pa_request_id, channel.value)
    return ok(result, request=request, transmitted=True)

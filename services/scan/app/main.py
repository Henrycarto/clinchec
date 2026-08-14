"""Clinchec Scan — FastAPI application entrypoint.

Boots the NLP pipeline once at startup (model load is ~1s and must not land on
a physician's first request), installs the shared error handlers so every
response — including validation failures — carries the `{ data, error, meta }`
envelope, and exposes `/health` for the ECS/compose health check.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.envelope import ApiError, Envelope, fail, ok
from app.routers import extract as extract_router
from app.schemas import HealthResult

SERVICE_VERSION = "0.1.0"

settings = get_settings()

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("clinchec.scan")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.models.soap_parser import get_parser

    app.state.service_name = settings.service_name
    app.state.service_version = SERVICE_VERSION

    logger.info("Loading spaCy pipeline %r", settings.active_spacy_model)
    started = time.perf_counter()
    parser = get_parser(settings.active_spacy_model)
    app.state.pipeline_loaded = True
    app.state.spacy_pipes = parser.nlp.pipe_names
    logger.info(
        "Pipeline ready in %.0f ms (components: %s)",
        (time.perf_counter() - started) * 1000,
        ", ".join(parser.nlp.pipe_names) or "none",
    )

    yield

    logger.info("Clinchec Scan shutting down")


app = FastAPI(
    title="Clinchec Scan",
    description=(
        "NLP microservice that reads unstructured SOAP notes and extracts the "
        "diagnosis codes, procedure codes and clinical justification language a "
        "prior authorization turns on."
    ),
    version=SERVICE_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.state.service_name = settings.service_name
app.state.service_version = SERVICE_VERSION
app.state.pipeline_loaded = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a request ID and emit one structured access log per request."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id

    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000

    response.headers["X-Request-ID"] = request_id
    # Note text is PHI and is never logged — only shape and timing.
    logger.info(
        "%s %s -> %d in %.0f ms [%s]",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        request_id,
    )
    return response


# ---------------------------------------------------------------------------
# Error handling — every failure keeps the envelope shape
# ---------------------------------------------------------------------------

@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError):
    return fail(
        exc.code,
        exc.message,
        status_code=exc.status_code,
        details=exc.details,
        request=request,
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    # Pydantic puts the originating exception object in `ctx`, which is not
    # JSON-serialisable — and could echo submitted note text back. Keep only
    # the location and the message.
    issues = [
        {
            "field": ".".join(str(part) for part in error.get("loc", ())),
            "message": error.get("msg", "invalid value"),
            "type": error.get("type", "value_error"),
        }
        for error in exc.errors()
    ]
    return fail(
        "validation_error",
        "The request body did not match the expected schema.",
        status_code=422,
        details={"issues": issues},
        request=request,
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException):
    return fail(
        "http_error",
        str(exc.detail),
        status_code=exc.status_code,
        request=request,
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    # Log the traceback, return nothing that could echo PHI back to the caller.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return fail(
        "internal_error",
        "An unexpected error occurred while processing the note.",
        status_code=500,
        request=request,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(extract_router.router)


@app.get("/health", response_model=Envelope[HealthResult], tags=["ops"])
async def health(request: Request) -> Envelope[HealthResult]:
    pipeline_loaded = bool(getattr(app.state, "pipeline_loaded", False))
    return ok(
        HealthResult(
            status="ok" if pipeline_loaded else "degraded",
            service=settings.service_name,
            version=SERVICE_VERSION,
            spacy_model=settings.active_spacy_model,
            pipeline_loaded=pipeline_loaded,
            justification_enabled=bool(
                settings.justification_enabled and settings.openai_api_key
            ),
        ),
        request=request,
    )

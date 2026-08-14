"""Clinchec Forms — FastAPI application entrypoint."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.envelope import ApiError, Envelope, fail, ok
from app.mappers import form_registry
from app.routers import submit as submit_router
from app.schemas import HealthResult

SERVICE_VERSION = "0.1.0"

settings = get_settings()

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("clinchec.forms")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.service_name = settings.service_name
    app.state.service_version = SERVICE_VERSION
    logger.info(
        "Clinchec Forms ready (%d form schemas across %s)",
        len(form_registry.ALL_FORMS),
        ", ".join(form_registry.registered_payers()),
    )
    yield
    logger.info("Clinchec Forms shutting down")


app = FastAPI(
    title="Clinchec Forms",
    description=(
        "One-click auto-population system that maps extracted clinical data to "
        "the correct prior-authorization form fields and submits or exports."
    ),
    version=SERVICE_VERSION,
    lifespan=lifespan,
)

app.state.service_name = settings.service_name
app.state.service_version = SERVICE_VERSION

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
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s -> %d in %.0f ms [%s]",
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
        request_id,
    )
    return response


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError):
    return fail(
        exc.code, exc.message, status_code=exc.status_code, details=exc.details, request=request
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
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
    return fail("http_error", str(exc.detail), status_code=exc.status_code, request=request)


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return fail(
        "internal_error",
        "An unexpected error occurred while preparing the form.",
        status_code=500,
        request=request,
    )


app.include_router(submit_router.router)


@app.get("/health", response_model=Envelope[HealthResult], tags=["ops"])
async def health(request: Request) -> Envelope[HealthResult]:
    live_reachable = await _ping_live()
    return ok(
        HealthResult(
            status="ok",
            service=settings.service_name,
            version=SERVICE_VERSION,
            registered_forms=len(form_registry.ALL_FORMS),
            payers=form_registry.registered_payers(),
            live_service_reachable=live_reachable,
        ),
        request=request,
    )


async def _ping_live() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{settings.live_service_url.rstrip('/')}/health")
        return response.status_code == 200
    except httpx.HTTPError:
        # Live being down degrades rule freshness, not form population.
        return False

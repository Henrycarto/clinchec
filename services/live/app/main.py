"""Clinchec Live — FastAPI read API over the payer rules database.

The crawling itself happens in Celery workers; this process serves what they
wrote. Scan calls `/rules/{payer}/{cpt}` on every scored note, so these reads
are the hot path and are deliberately kept to a single indexed query.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db
from app.config import get_settings
from app.envelope import ApiError, Envelope, fail, ok
from app.payers import REGISTERED_SLUGS, build_adapters
from app.schemas import HealthResult, PayerRuleResult, PayerSummary

SERVICE_VERSION = "0.1.0"

settings = get_settings()

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("clinchec.live")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.service_name = settings.service_name
    app.state.service_version = SERVICE_VERSION
    db.init_engine(settings)
    await _migrate()
    await _seed_if_empty()
    logger.info("Clinchec Live ready (payers: %s)", ", ".join(REGISTERED_SLUGS))
    yield
    await db.dispose_engine()
    logger.info("Clinchec Live shutting down")


async def _migrate() -> None:
    """Bring the schema up to date before serving anything.

    Deliberately fatal on failure, unlike seeding below. A service that starts
    against a half-migrated schema serves reads that look fine right up until
    they touch the column that never arrived — and the whole point of this step
    is that nothing was tracking whether migrations had been applied.

    Also invoked as a one-shot `migrate` step in compose so the Celery worker
    cannot reach a database Live has not finished migrating. Both call the same
    function; running it twice is a lock and a SELECT.
    """
    if not settings.run_migrations_on_startup:
        logger.info("Startup migrations disabled; assuming the schema is current")
        return

    from app.migrations import apply_pending

    await apply_pending(settings)


async def _seed_if_empty() -> None:
    """Load each adapter's published-criteria seed set on a cold database.

    Without this a fresh deployment serves 404s until the first scheduled crawl
    completes — up to six hours during which every scan silently falls back to
    the national baseline. Seeding is idempotent (checksum-compared) and only
    runs when the table is genuinely empty, so a restart against a populated
    database is a no-op.
    """
    if not settings.seed_rules_on_startup:
        return

    try:
        if await db.count_rules() > 0:
            return

        from app.tasks.rules_sync import apply_drafts

        logger.info("Rules table is empty — loading adapter seed sets")
        created = 0
        for adapter in build_adapters(settings):
            report = await apply_drafts(adapter.slug, adapter.seed_rules())
            created += report.created
        logger.info("Seeded %d payer rules", created)
    except Exception:  # noqa: BLE001 — seeding must never block startup
        logger.exception("Rule seeding failed; Live will serve whatever is stored")


app = FastAPI(
    title="Clinchec Live",
    description=(
        "Background engine that polls payer portals and maintains an "
        "up-to-date prior-authorization rules database per insurer and procedure."
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
        "The request did not match the expected schema.",
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
    return fail("internal_error", "An unexpected error occurred.", status_code=500, request=request)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/payers", response_model=Envelope[list[PayerSummary]], tags=["rules"])
async def get_payers(request: Request) -> Envelope[list[PayerSummary]]:
    """List every payer Clinchec tracks, with rule counts and crawl freshness."""
    payers = await db.list_payers()
    return ok(payers, request=request, registered_adapters=list(REGISTERED_SLUGS))


@app.get(
    "/rules/{payer_slug}/{cpt_code}",
    response_model=Envelope[PayerRuleResult],
    tags=["rules"],
    summary="Current prior-authorization criteria for one payer and procedure",
)
async def get_rule(
    payer_slug: str,
    cpt_code: str,
    request: Request,
    plan_type: str | None = Query(
        default=None,
        description="Optional plan type ('commercial', 'medicare_advantage', …). "
        "An exact plan match is preferred over the payer's default criteria.",
    ),
) -> Envelope[PayerRuleResult]:
    rule = await db.get_rule(payer_slug.lower(), cpt_code, plan_type)
    if rule is None:
        raise ApiError(
            "rule_not_found",
            f"No synced criteria for {payer_slug}/{cpt_code}.",
            status_code=404,
            details={"payer_slug": payer_slug, "cpt_code": cpt_code},
        )
    return ok(rule, request=request, stale=rule.staleness_hours > 24 * 30)


@app.get(
    "/rules/{payer_slug}",
    response_model=Envelope[list[PayerRuleResult]],
    tags=["rules"],
)
async def list_payer_rules(
    payer_slug: str,
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
    include_retired: bool = Query(
        default=False,
        description="Include rules withdrawn because their source document "
        "stopped being published. For inspection only — nothing that scores a "
        "request should ask for a retired rule.",
    ),
) -> Envelope[list[PayerRuleResult]]:
    if payer_slug.lower() not in REGISTERED_SLUGS:
        raise ApiError(
            "unknown_payer",
            f"{payer_slug!r} is not a registered payer.",
            status_code=404,
            details={"registered": list(REGISTERED_SLUGS)},
        )
    rules = await db.list_rules(payer_slug.lower(), limit, include_retired)
    return ok(rules, request=request, count=len(rules))


@app.post("/crawl/{payer_slug}", response_model=Envelope[dict], tags=["ops"])
async def trigger_crawl(payer_slug: str, request: Request) -> Envelope[dict]:
    """Queue an out-of-band crawl for one payer.

    Returns immediately with the task id — a full portal sweep runs for
    minutes, well past any sensible HTTP timeout.
    """
    if payer_slug.lower() not in REGISTERED_SLUGS:
        raise ApiError(
            "unknown_payer",
            f"{payer_slug!r} is not a registered payer.",
            status_code=404,
            details={"registered": list(REGISTERED_SLUGS)},
        )

    from app.tasks.payer_crawler import crawl_payer

    task = crawl_payer.delay(payer_slug.lower())
    return ok({"task_id": task.id, "payer_slug": payer_slug.lower()}, request=request)


@app.get("/health", response_model=Envelope[HealthResult], tags=["ops"])
async def health(request: Request) -> Envelope[HealthResult]:
    database_reachable = await db.ping(settings)
    broker_reachable = _ping_broker()
    return ok(
        HealthResult(
            status="ok" if database_reachable else "degraded",
            service=settings.service_name,
            version=SERVICE_VERSION,
            database_reachable=database_reachable,
            broker_reachable=broker_reachable,
            registered_payers=list(REGISTERED_SLUGS),
            offline_seed_mode=settings.offline_seed_mode,
        ),
        request=request,
    )


def _ping_broker() -> bool:
    try:
        import redis

        client = redis.Redis.from_url(settings.celery_broker_url, socket_connect_timeout=2)
        return bool(client.ping())
    except Exception as exc:  # noqa: BLE001 — health check must not raise
        logger.warning("Broker ping failed: %s", exc)
        return False

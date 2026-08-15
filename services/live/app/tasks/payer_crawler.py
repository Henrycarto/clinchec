"""Celery tasks that poll payer portals.

Celery is synchronous; the adapters are async. Rather than sprinkle
`asyncio.run` through the task bodies, each task is a thin sync wrapper around
an async implementation, which keeps the crawl logic testable without a broker.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from celery import shared_task

from app.config import get_settings
from app.db import dispose_engine, init_engine, record_crawl
from app.payers import build_adapter, build_adapters
from app.schemas import CrawlResult, CrawlStatus
from app.tasks.rules_sync import apply_drafts

logger = logging.getLogger(__name__)


def _run(coro):
    """Run an async implementation from a synchronous Celery task."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@shared_task(name="app.tasks.payer_crawler.crawl_all_payers", bind=True)
def crawl_all_payers(self) -> list[dict]:  # noqa: ANN001, ARG001
    """Crawl every registered payer. Failures are per-payer, never fatal."""
    return _run(_crawl_all())


@shared_task(name="app.tasks.payer_crawler.crawl_payer", bind=True, max_retries=3)
def crawl_payer(self, payer_slug: str) -> dict:  # noqa: ANN001
    """Crawl a single payer. Retried with backoff on transport errors."""
    try:
        return _run(_crawl_one(payer_slug))
    except httpx.TransportError as exc:
        # Network-level failure is worth retrying; a parse failure is not, and
        # `safe_sync` has already swallowed those into a FAILED result.
        raise self.retry(exc=exc, countdown=300) from exc


# ---------------------------------------------------------------------------
# Async implementations
# ---------------------------------------------------------------------------

async def _crawl_all() -> list[dict]:
    settings = get_settings()
    init_engine(settings)
    adapters = build_adapters(settings)

    results: list[CrawlResult] = []
    try:
        async with _client(settings) as client:
            # Sequential by design: concurrent crawls across payers would be
            # fine for the portals but would blow past the per-host politeness
            # budget the moment two adapters shared a CDN.
            for adapter in adapters:
                result, drafts = await adapter.safe_sync(client)
                # Called even with no drafts. A complete crawl that produced
                # nothing is how a payer withdrawing every policy we track
                # looks, and skipping the sweep here would make that the one
                # case retirement never reaches.
                report = await apply_drafts(adapter.slug, drafts, result)
                result.rules_changed = report.created + report.updated
                await record_crawl(result)
                results.append(result)
                logger.info(
                    "%s: %s — %d rules seen, %d changed, %d retired, %d counting",
                    adapter.slug,
                    result.status.value,
                    result.rules_seen,
                    result.rules_changed,
                    report.retired,
                    report.retiring,
                )
    finally:
        await dispose_engine()

    return [result.model_dump(mode="json") for result in results]


async def _crawl_one(payer_slug: str) -> dict:
    settings = get_settings()
    init_engine(settings)
    adapter = build_adapter(payer_slug, settings)

    if adapter is None:
        logger.error("No adapter registered for payer %r", payer_slug)
        return {"payer_slug": payer_slug, "status": CrawlStatus.FAILED.value,
                "error": f"no adapter registered for {payer_slug!r}"}

    try:
        async with _client(settings) as client:
            result, drafts = await adapter.safe_sync(client)
            report = await apply_drafts(adapter.slug, drafts, result)
            result.rules_changed = report.created + report.updated
            await record_crawl(result)
    finally:
        await dispose_engine()

    return result.model_dump(mode="json")


def _client(settings) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.payer_http_timeout_seconds),
        headers={
            "User-Agent": settings.payer_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf",
            "Accept-Language": "en-US,en;q=0.9",
        },
        # One connection per host keeps us inside the politeness budget even if
        # an adapter is later changed to fetch concurrently.
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
        follow_redirects=True,
    )

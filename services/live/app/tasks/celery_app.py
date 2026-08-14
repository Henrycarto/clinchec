"""Celery application and beat schedule for Clinchec Live.

The worker and beat containers both boot from this module
(`celery -A app.tasks.celery_app worker|beat`).
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "clinchec_live",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.payer_crawler", "app.tasks.rules_sync"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Payer portals are slow and occasionally hang; a task that outlives its
    # window must be killed rather than pile up behind the next schedule.
    task_soft_time_limit=25 * 60,
    task_time_limit=30 * 60,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_retry_delay=300,
    task_max_retries=3,
    result_expires=60 * 60 * 24,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    # Full sweep of every payer, staggered off the hour so a restart storm does
    # not hit three portals simultaneously.
    "crawl-all-payers": {
        "task": "app.tasks.payer_crawler.crawl_all_payers",
        "schedule": crontab(minute=17, hour=f"*/{max(1, settings.payer_poll_interval_minutes // 60)}"),
    },
    # Nightly freshness audit: flags rules nobody has re-verified in 30 days.
    "audit-rule-freshness": {
        "task": "app.tasks.rules_sync.audit_freshness",
        "schedule": crontab(minute=45, hour=3),
    },
}

__all__ = ["celery_app"]

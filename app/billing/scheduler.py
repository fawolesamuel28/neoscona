"""Scheduled recurring-charge runner for Flutterwave-based subscriptions.

This module exposes `setup_scheduler()` which registers a daily/cron job to
charge due tenants. APScheduler is optional; if it's not installed we'll log
and skip scheduling (useful for test/dev environments).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def charge_due_tenants() -> None:
    """Find tenants due for billing and attempt renewal via Flutterwave tokens."""
    try:
        from app.services.billing import process_automated_renewals
        await process_automated_renewals()
    except Exception as e:
        logger.error("Failed to run automated renewals: %s", e)


def setup_scheduler() -> None:
    """Register the recurring-job with APScheduler if available."""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler(timezone="Africa/Lagos")
        # Run daily at 08:00 Africa/Lagos
        scheduler.add_job(lambda: __import__("asyncio").create_task(charge_due_tenants()), "cron", hour=8, minute=0)
        scheduler.start()
        logger.info("Billing scheduler started")
    except Exception:
        logger.info("apscheduler not available; skipping billing scheduler")

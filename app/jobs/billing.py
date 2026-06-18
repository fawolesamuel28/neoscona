"""Billing/usage background jobs (Celery beat).

- rollup_usage: reconcile usage_counters from the event log for the current period.
- expire_trials: move lapsed trials to 'past_due' (soft — no service cut this build).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.db.supabase import get_supabase
from app.services.usage import current_period

logger = logging.getLogger(__name__)


async def rollup_usage() -> None:
    """Recompute the current period's usage_counters from usage_events."""
    try:
        db = get_supabase()
        start, end = current_period()

        def _run():
            return db.rpc("rollup_usage_counters", {
                "p_start": start.isoformat(),
                "p_end": end.isoformat(),
            }).execute()

        await asyncio.to_thread(_run)
        logger.info("Usage rollup complete for %s..%s", start, end)
    except Exception as exc:
        logger.error("rollup_usage failed: %s", exc)


async def expire_trials() -> None:
    """Flip trials whose window has passed to 'past_due' (soft state)."""
    try:
        db = get_supabase()
        now = datetime.now(timezone.utc).isoformat()

        def _run():
            return (
                db.table("tenants")
                .update({"subscription_status": "past_due", "updated_at": now})
                .eq("subscription_status", "trialing")
                .lt("trial_ends_at", now)
                .execute()
            )

        res = await asyncio.to_thread(_run)
        n = len(res.data or [])
        if n:
            logger.info("Expired %d trial(s) to past_due", n)
    except Exception as exc:
        logger.error("expire_trials failed: %s", exc)

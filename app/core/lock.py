"""Minimal Redis distributed lock for scheduled billing jobs.

Celery beat has no built-in duplicate-fire protection, and this codebase's
scheduled jobs have historically run duplicated (see app/billing/scheduler.py,
removed in favor of Celery beat) when more than one process ends up running the
same cron. `with_lock` wraps a task body in a `SET NX EX` guard so overlapping
beat/worker instances only ever execute it once per window.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.cache.redis import get_redis_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def with_lock(key: str, ttl_seconds: int = 300) -> AsyncIterator[bool]:
    """Yield True if the lock was acquired, False otherwise. Never raises.

    On any Redis error the lock fails open (yields True) — a scheduling job
    being skipped is worse for billing correctness than an unlikely duplicate
    run, and Redis is already optional infrastructure elsewhere in this app.
    """
    lock_key = f"lock:{key}"
    acquired = False
    try:
        client = await get_redis_client()
        acquired = bool(await client.set(lock_key, "1", nx=True, ex=ttl_seconds))
    except Exception as exc:
        logger.warning("Lock acquire failed for %s (failing open): %s", key, exc)
        acquired = True

    try:
        yield acquired
    finally:
        if acquired:
            try:
                client = await get_redis_client()
                await client.delete(lock_key)
            except Exception:
                pass

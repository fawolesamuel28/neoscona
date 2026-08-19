"""Ledger, overage pricing, and the distributed lock — no live DB/Redis.

Follows the monkeypatch style in tests/test_voice.py: fake the seam the module
calls out to, drive coroutines with asyncio.run.
"""

from __future__ import annotations

import asyncio

import pytest

from app.billing import plans
from app.core import lock


# ─── overage pricing ──────────────────────────────────────────────────────────

def test_overage_price_kobo_known_plan_key():
    assert plans.overage_price_kobo("growth", "messages") == 40
    assert plans.overage_price_kobo("growth", "voice_minutes") == 3000


def test_overage_price_kobo_trial_is_none():
    assert plans.overage_price_kobo("trial", "messages") is None
    assert plans.overage_price_kobo("trial", "voice_minutes") is None


def test_overage_price_kobo_unknown_plan_falls_back_to_trial():
    assert plans.overage_price_kobo("nonexistent-plan", "messages") is None


# ─── distributed lock ─────────────────────────────────────────────────────────

class _FakeRedis:
    """In-memory stand-in for the subset of redis.asyncio used by with_lock."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)


def test_with_lock_acquires_when_free(monkeypatch):
    fake = _FakeRedis()

    async def _get_client():
        return fake

    monkeypatch.setattr(lock, "get_redis_client", _get_client)

    async def _run():
        async with lock.with_lock("test-job") as acquired:
            return acquired

    assert asyncio.run(_run()) is True


def test_with_lock_blocks_second_holder(monkeypatch):
    fake = _FakeRedis()

    async def _get_client():
        return fake

    monkeypatch.setattr(lock, "get_redis_client", _get_client)

    async def _run():
        async with lock.with_lock("test-job", ttl_seconds=60) as first:
            async with lock.with_lock("test-job", ttl_seconds=60) as second:
                return first, second

    first, second = asyncio.run(_run())
    assert first is True
    assert second is False


def test_with_lock_released_after_context_exits(monkeypatch):
    fake = _FakeRedis()

    async def _get_client():
        return fake

    monkeypatch.setattr(lock, "get_redis_client", _get_client)

    async def _run():
        async with lock.with_lock("test-job", ttl_seconds=60):
            pass
        async with lock.with_lock("test-job", ttl_seconds=60) as second_acquire:
            return second_acquire

    assert asyncio.run(_run()) is True


def test_with_lock_fails_open_on_redis_error(monkeypatch):
    async def _boom():
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(lock, "get_redis_client", _boom)

    async def _run():
        async with lock.with_lock("test-job") as acquired:
            return acquired

    assert asyncio.run(_run()) is True

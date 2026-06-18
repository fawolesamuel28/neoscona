from __future__ import annotations

import json
import os
from typing import Any, Optional

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.exceptions import RedisError
from app.core.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONVERSATION_TTL: int = 60 * 60 * 24          # 24 hours per inactive conversation
MAX_HISTORY_LENGTH: int = 20                    # cap messages kept in memory
STAGE_TTL: int = 60 * 60 * 24 * 7             # stage persists 7 days

VALID_STAGES = frozenset({"new", "qualifying", "qualified", "booking", "done"})

# ---------------------------------------------------------------------------
# Connection pool — one shared pool for the entire process lifetime
# ---------------------------------------------------------------------------
_pool: Optional[Redis] = None


async def get_redis_client() -> Redis:
    """
    Return (and lazily create) a single async Redis connection pool.
    Using a pool avoids the overhead of creating a new connection per call.
    """
    global _pool
    if _pool is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379")
        clean_url = url.split("?")[0]
        _pool = aioredis.from_url(
            clean_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            max_connections=20,
        )
        logger.info("Redis connection pool initialised → %s", clean_url)
    return _pool


async def close_redis_client() -> None:
    """Call this on app shutdown to cleanly drain the pool."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
        logger.info("Redis connection pool closed.")


def reset_redis_pool() -> None:
    """Sync reset for Celery workers — asyncio.run() closes the loop each task."""
    global _pool
    _pool = None


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def _conversation_key(phone_number: str) -> str:
    return f"reva:conversation:{phone_number}"


def _stage_key(phone_number: str) -> str:
    return f"reva:stage:{phone_number}"


def _lead_data_key(phone_number: str) -> str:
    return f"reva:lead:{phone_number}"


# ---------------------------------------------------------------------------
# Anti-spam & Deduplication
# ---------------------------------------------------------------------------

async def is_rate_limited(phone_number: str) -> bool:
    """
    Max 10 messages per minute per phone number.
    Protects against spam and runaway LLM costs.
    """
    try:
        client = await get_redis_client()
        key = f"reva:ratelimit:{phone_number}"
        count = await client.incr(key)
        
        if count == 1:
            await client.expire(key, 60)  # Reset every 60 seconds
        
        return count > 10
    except Exception as e:
        logger.error(f"Rate limit check failed: {e}")
        return False  # Fail open


async def is_already_received(message_id: str) -> bool:
    """Gateway-level dedup: has this webhook payload already been received?"""
    try:
        client = await get_redis_client()
        return await client.exists(f"reva:received:{message_id}") > 0
    except Exception as e:
        logger.error(f"Dedup check failed: {e}")
        return False


async def mark_as_received(message_id: str) -> None:
    """Mark a message_id as received at the gateway (1-hour TTL)."""
    try:
        client = await get_redis_client()
        await client.setex(f"reva:received:{message_id}", 3600, "1")
    except Exception as e:
        logger.error(f"Dedup mark failed: {e}")


async def is_already_done(message_id: str) -> bool:
    """Worker-level dedup: has this message already been fully processed?"""
    try:
        client = await get_redis_client()
        return await client.exists(f"reva:done:{message_id}") > 0
    except Exception as e:
        logger.error(f"Done check failed: {e}")
        return False


async def mark_as_done(message_id: str) -> None:
    """Mark a message_id as fully processed (24-hour TTL)."""
    try:
        client = await get_redis_client()
        await client.setex(f"reva:done:{message_id}", 86400, "1")
    except Exception as e:
        logger.error(f"Done mark failed: {e}")


# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------

async def get_conversation_history(phone_number: str) -> list[dict[str, str]]:
    """
    Retrieve the full conversation history for a lead.
    Returns an empty list if no history exists or Redis is unavailable.
    """
    try:
        client = await get_redis_client()
        raw = await client.get(_conversation_key(phone_number))
        return json.loads(raw) if raw else []
    except RedisError as exc:
        logger.error("Redis GET conversation error [%s]: %s", phone_number, exc)
        return []


async def save_conversation_history(
    phone_number: str,
    history: list[dict[str, str]],
) -> None:
    """
    Persist conversation history, trimming to MAX_HISTORY_LENGTH messages.
    TTL is reset on every call so active conversations never expire mid-chat.
    """
    trimmed = history[-MAX_HISTORY_LENGTH:]
    try:
        client = await get_redis_client()
        await client.setex(
            _conversation_key(phone_number),
            CONVERSATION_TTL,
            json.dumps(trimmed, ensure_ascii=False),
        )
    except RedisError as exc:
        logger.error("Redis SET conversation error [%s]: %s", phone_number, exc)


async def clear_conversation(phone_number: str) -> None:
    """Delete conversation history — used when a lead restarts or qualifies out."""
    try:
        client = await get_redis_client()
        await client.delete(_conversation_key(phone_number))
        logger.info("Conversation cleared for %s", phone_number)
    except RedisError as exc:
        logger.error("Redis DEL conversation error [%s]: %s", phone_number, exc)


# ---------------------------------------------------------------------------
# Stage tracking
# ---------------------------------------------------------------------------

async def get_lead_stage(phone_number: str) -> str:
    """
    Return the lead's current funnel stage.
    Stages: new → qualifying → qualified → booking → done
    Defaults to 'new' on first contact or Redis failure.
    """
    try:
        client = await get_redis_client()
        stage = await client.get(_stage_key(phone_number))
        return stage if stage in VALID_STAGES else "new"
    except RedisError as exc:
        logger.error("Redis GET stage error [%s]: %s", phone_number, exc)
        return "new"


async def set_lead_stage(phone_number: str, stage: str) -> None:
    """Update the lead's funnel stage."""
    if stage not in VALID_STAGES:
        logger.warning("Attempted to set invalid stage '%s' for %s", stage, phone_number)
        return
    try:
        client = await get_redis_client()
        await client.setex(_stage_key(phone_number), STAGE_TTL, stage)
        logger.info("Stage updated: %s → %s", phone_number, stage)
    except RedisError as exc:
        logger.error("Redis SET stage error [%s]: %s", phone_number, exc)


# ---------------------------------------------------------------------------
# Extracted lead data cache (partial profile before DB write)
# ---------------------------------------------------------------------------

async def get_lead_data(phone_number: str) -> dict[str, Any]:
    """Return the accumulated extracted lead data dict, or empty dict."""
    try:
        client = await get_redis_client()
        raw = await client.get(_lead_data_key(phone_number))
        return json.loads(raw) if raw else {}
    except RedisError as exc:
        logger.error("Redis GET lead_data error [%s]: %s", phone_number, exc)
        return {}


async def update_lead_data(phone_number: str, updates: dict[str, Any]) -> None:
    """
    Merge new extracted fields into the cached lead profile.
    Only non-None values overwrite existing data (preserves prior extractions).
    """
    current = await get_lead_data(phone_number)
    merged = {**current, **{k: v for k, v in updates.items() if v is not None}}
    try:
        client = await get_redis_client()
        await client.setex(
            _lead_data_key(phone_number),
            CONVERSATION_TTL,
            json.dumps(merged, ensure_ascii=False),
        )
    except RedisError as exc:
        logger.error("Redis SET lead_data error [%s]: %s", phone_number, exc)


# ---------------------------------------------------------------------------
# Matched units cache (dashboard + fallback when lead_unit_matches missing)
# ---------------------------------------------------------------------------

def _matches_key(phone_number: str) -> str:
    return f"reva:matches:{phone_number}"


async def cache_lead_matches(phone_number: str, matches: list[dict[str, Any]]) -> None:
    try:
        client = await get_redis_client()
        await client.setex(
            _matches_key(phone_number),
            CONVERSATION_TTL,
            json.dumps(matches, ensure_ascii=False),
        )
    except RedisError as exc:
        logger.error("Redis SET matches error [%s]: %s", phone_number, exc)


async def get_cached_lead_matches(phone_number: str) -> list[dict[str, Any]]:
    try:
        client = await get_redis_client()
        raw = await client.get(_matches_key(phone_number))
        return json.loads(raw) if raw else []
    except RedisError as exc:
        logger.error("Redis GET matches error [%s]: %s", phone_number, exc)
        return []

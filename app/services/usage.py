"""Usage metering — records billable events and exposes current-cycle counters.

Onboarding-first: this records overage but never blocks (soft limits). The message
meter is fire-and-forget so metering can never break the message pipeline.

Period model: calendar month. The daily Celery rollup reconciles `usage_counters`
from the append-only `usage_events` log.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any, Optional

from app.billing.plans import limit as plan_limit
from app.core.tenant import require_tenant
from app.db.supabase import get_supabase

logger = logging.getLogger(__name__)

# Soft warning threshold (fraction of limit) surfaced as a UI banner.
WARN_AT = 0.8

# usage_event type -> usage_counters increment column
_EVENT_TO_COLUMN = {
    "message": "p_messages",
    "voice_minute": "p_voice",
    "seat": "p_seats",
}


def current_period(today: Optional[date] = None) -> tuple[date, date]:
    """First and last day of the current calendar month."""
    today = today or date.today()
    start = today.replace(day=1)
    nxt = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, nxt - timedelta(days=1)


async def record_usage(tenant_id: Optional[str], event_type: str = "message", quantity: float = 1) -> None:
    """Log a billable event and increment the live-period counter. Never raises.

    `tenant_id` is required — metering is skipped (with a warning) rather than
    attributed to the wrong workspace if the caller failed to resolve it.
    """
    try:
        tenant_id = require_tenant(tenant_id)
        db = get_supabase()
        start, end = current_period()
        col = _EVENT_TO_COLUMN.get(event_type, "p_messages")

        def _write():
            db.table("usage_events").insert({
                "tenant_id": tenant_id,
                "event_type": event_type,
                "quantity": quantity,
            }).execute()
            db.rpc("increment_usage_counter", {
                "p_tenant": tenant_id,
                "p_period_start": start.isoformat(),
                "p_period_end": end.isoformat(),
                "p_messages": 0,
                "p_voice": 0,
                "p_seats": 0,
                col: quantity,
            }).execute()

        await asyncio.to_thread(_write)
    except Exception as exc:  # metering must never break the pipeline
        logger.warning("record_usage failed for tenant %s (%s): %s", tenant_id, event_type, exc)


async def _fetch_counter(tenant_id: str) -> dict[str, Any]:
    db = get_supabase()
    start, _ = current_period()

    def _get():
        return (
            db.table("usage_counters")
            .select("messages, voice_minutes, seats, period_start, period_end")
            .eq("tenant_id", tenant_id)
            .eq("period_start", start.isoformat())
            .limit(1)
            .execute()
        )

    res = await asyncio.to_thread(_get)
    return (res.data or [{}])[0] if res.data else {}


async def _plan_for_tenant(tenant_id: str) -> str:
    db = get_supabase()

    def _get():
        return db.table("tenants").select("plan").eq("id", tenant_id).limit(1).execute()

    res = await asyncio.to_thread(_get)
    return (res.data[0]["plan"] if res.data else "trial") or "trial"


def _usage_for_key(used: float, cap: Optional[int]) -> dict[str, Any]:
    pct = None if cap in (None, 0) else round(used / cap, 4)
    return {
        "used": used,
        "limit": cap,                      # None = unlimited
        "pct": pct,
        "over": cap is not None and used > cap,
        "warn": pct is not None and pct >= WARN_AT,
    }


async def get_usage(tenant_id: str, plan: Optional[str] = None) -> dict[str, Any]:
    """Current-cycle usage vs plan limits for messages / voice_minutes / seats."""
    try:
        tenant_id = require_tenant(tenant_id)
        plan = plan or await _plan_for_tenant(tenant_id)
        counter = await _fetch_counter(tenant_id)
        return {
            "plan": plan,
            "period_start": counter.get("period_start"),
            "period_end": counter.get("period_end"),
            "messages": _usage_for_key(counter.get("messages", 0) or 0, plan_limit(plan, "messages")),
            "voice_minutes": _usage_for_key(counter.get("voice_minutes", 0) or 0, plan_limit(plan, "voice_minutes")),
            "seats": _usage_for_key(counter.get("seats", 0) or 0, plan_limit(plan, "seats")),
        }
    except Exception as exc:
        logger.warning("get_usage failed for tenant %s: %s", tenant_id, exc)
        return {"plan": plan or "trial", "messages": _usage_for_key(0, None)}


async def over_limit(tenant_id: str, key: str = "messages", plan: Optional[str] = None) -> bool:
    """Soft check — true when a tenant has exceeded the quota for `key` (does not block)."""
    usage = await get_usage(tenant_id, plan=plan)
    return bool(usage.get(key, {}).get("over"))

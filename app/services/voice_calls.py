"""Voice call log — the full per-call record behind the Neoscona Voice console.

Complements `app/services/elevenlabs_leads.py` (the qualified-capture subset). Where the
leads service stores extracted fields for pipeline import, this stores EVERY receptionist
call: caller, status, duration, the full transcript turn-array, and the AI summary.

The post-call webhook (`app/webhooks/voice_elevenlabs.py`) writes here under the
service-role client (RLS bypassed), having already resolved the owning tenant from the
channel registry. The console API reads here as the authenticated user. As in the leads
service, every query carries an explicit `.eq("tenant_id", …)` — under the service-role
client that filter is the ONLY guard against a cross-tenant read, so it is never optional.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.tenant import require_tenant
from app.db.supabase import get_supabase

logger = logging.getLogger(__name__)

SOURCE_ELEVENLABS = "elevenlabs_receptionist"

# Statuses we recognise; anything else is surfaced verbatim.
_KNOWN_STATUSES = ("completed", "failed", "no-data", "unknown")


def _transcript_list(raw: Any) -> list[dict[str, Any]]:
    """The transcript is stored as a JSONB array; tolerate null/garbage."""
    if isinstance(raw, list):
        return raw
    return []


def normalize_voice_call(row: dict[str, Any], *, include_transcript: bool = False) -> dict[str, Any]:
    """Shape a voice_calls row for the API.

    List responses omit the (potentially large) transcript array and expose a
    `turn_count` instead; the detail endpoint passes `include_transcript=True`.
    """
    transcript = _transcript_list(row.get("transcript"))
    shaped: dict[str, Any] = {
        "id": row.get("id"),
        "conversation_id": row.get("conversation_id"),
        "caller_number": row.get("caller_number"),
        "e164": row.get("e164"),
        "direction": row.get("direction") or "inbound",
        "status": row.get("status") or "completed",
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "duration_secs": row.get("duration_secs"),
        "has_audio": bool(row.get("has_audio")),
        "recording_url": row.get("recording_url"),
        "summary": row.get("summary"),
        "turn_count": len(transcript),
        "created_at": row.get("created_at"),
        "source": SOURCE_ELEVENLABS,
    }
    if include_transcript:
        shaped["transcript"] = transcript
    return shaped


def _sanitize_search(term: str) -> str:
    """Strip PostgREST filter metacharacters from a free-text search term.

    The term is interpolated into an `.or_(col.ilike.*term*)` filter; characters
    like `,` `*` `(` `)` `%` would otherwise let a caller alter the filter grammar.
    """
    return "".join(c for c in (term or "") if c not in ",*()%\\\"'") .strip()


async def upsert_call_log(
    tenant_id: str, conversation_id: str, fields: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Insert (or update on retry) a call log row.

    Idempotent on `(tenant_id, conversation_id)` so a retried webhook updates the same
    row. Best-effort: never raises — the webhook must not fail because the call log
    couldn't be written. `tenant_id` is the channel-resolved owner (never the body).
    """
    tenant_id = require_tenant(tenant_id)
    if not conversation_id:
        logger.warning("upsert_call_log without conversation_id (tenant=%s); skipping", tenant_id)
        return None
    try:
        db = get_supabase()
        row = {
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            # transcript is allowed to be an explicit [] (falsy but not None).
            **{k: v for k, v in fields.items() if v is not None},
        }

        def _upsert():
            return db.table("voice_calls").upsert(
                row, on_conflict="tenant_id,conversation_id"
            ).execute()

        res = await asyncio.to_thread(_upsert)
        if not res.data:
            return None
        return normalize_voice_call(res.data[0])
    except Exception as exc:
        logger.error(
            "Failed to upsert voice call (tenant=%s conv=%s): %s",
            tenant_id, conversation_id, exc,
        )
        return None


async def get_all_calls(
    tenant_id: str | None = None,
    *,
    status: Optional[str] = None,
    since: Optional[datetime] = None,
    search: Optional[str] = None,
) -> list[dict[str, Any]]:
    """All calls for a tenant, newest first. Optional status/since/search narrowing."""
    tenant_id = require_tenant(tenant_id)
    try:
        db = get_supabase()

        def _fetch():
            q = (
                db.table("voice_calls")
                .select("*")
                .eq("tenant_id", tenant_id)
            )
            if status:
                q = q.eq("status", status)
            if since is not None:
                q = q.gte("created_at", since.isoformat())
            if search:
                term = _sanitize_search(search)
                if term:
                    q = q.or_(f"caller_number.ilike.%{term}%,summary.ilike.%{term}%")
            return q.order("created_at", desc=True).execute()

        result = await asyncio.to_thread(_fetch)
        return [normalize_voice_call(row) for row in (result.data or [])]
    except Exception as exc:
        logger.error("Failed to fetch voice calls (tenant=%s): %s", tenant_id, exc)
        return []


async def get_call(
    id_or_conversation_id: str, tenant_id: str | None = None
) -> Optional[dict[str, Any]]:
    """Fetch one call (with transcript) by UUID PK or conversation_id.

    The tenant filter is applied on BOTH lookup branches — a caller can never read a
    call outside their workspace, and the API returns 404 (not 403) so a foreign
    conversation_id's existence is never confirmed.
    """
    tenant_id = require_tenant(tenant_id)
    key = (id_or_conversation_id or "").strip()
    if not key:
        return None
    try:
        db = get_supabase()

        # A voice_calls PK is a uuid (36 chars, 4 dashes); a conversation_id is not.
        looks_like_uuid = len(key) == 36 and key.count("-") == 4
        column = "id" if looks_like_uuid else "conversation_id"

        def _fetch():
            return (
                db.table("voice_calls")
                .select("*")
                .eq(column, key)
                .eq("tenant_id", tenant_id)
                .limit(1)
                .execute()
            )

        result = await asyncio.to_thread(_fetch)
        rows = result.data or []
        if not rows:
            return None
        return normalize_voice_call(rows[0], include_transcript=True)
    except Exception as exc:
        logger.error("Failed to fetch voice call %s (tenant=%s): %s", key, tenant_id, exc)
        return None


def _period_cutoff(period: str) -> datetime:
    now = datetime.now(timezone.utc)
    days = 30 if period == "month" else 7
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start


async def get_call_analytics(
    tenant_id: str | None = None, *, period: str = "week"
) -> dict[str, Any]:
    """Aggregate call volume/duration for the console overview.

    Pure read + in-Python rollup (per-tenant volumes are small). Voice minutes are
    derived from this call log so the analytics card matches the Calls list exactly.
    """
    tenant_id = require_tenant(tenant_id)
    period = "month" if period == "month" else "week"
    since = _period_cutoff(period)

    empty = {
        "period": period,
        "total_calls": 0,
        "total_duration_secs": 0,
        "avg_duration_secs": 0,
        "voice_minutes": 0.0,
        "by_day": [],
        "status_breakdown": {},
    }
    try:
        db = get_supabase()

        def _fetch():
            return (
                db.table("voice_calls")
                .select("status,duration_secs,created_at")
                .eq("tenant_id", tenant_id)
                .gte("created_at", since.isoformat())
                .order("created_at", desc=False)
                .execute()
            )

        result = await asyncio.to_thread(_fetch)
        rows = result.data or []
    except Exception as exc:
        logger.error("Failed to fetch voice analytics (tenant=%s): %s", tenant_id, exc)
        return empty

    # Seed by_day with every day in the window so the chart has no gaps.
    span = 30 if period == "month" else 7
    by_day: dict[str, dict[str, float]] = {}
    for i in range(span):
        day = (since + timedelta(days=i)).date().isoformat()
        by_day[day] = {"date": day, "calls": 0, "minutes": 0.0}

    total_duration = 0
    status_breakdown: dict[str, int] = {}
    for row in rows:
        dur = int(row.get("duration_secs") or 0)
        total_duration += dur
        st = row.get("status") or "unknown"
        status_breakdown[st] = status_breakdown.get(st, 0) + 1
        day = (row.get("created_at") or "")[:10]
        if day in by_day:
            by_day[day]["calls"] += 1
            by_day[day]["minutes"] = round(by_day[day]["minutes"] + dur / 60.0, 2)

    total_calls = len(rows)
    return {
        "period": period,
        "total_calls": total_calls,
        "total_duration_secs": total_duration,
        "avg_duration_secs": round(total_duration / total_calls) if total_calls else 0,
        "voice_minutes": round(total_duration / 60.0, 2),
        "by_day": list(by_day.values()),
        "status_breakdown": status_breakdown,
    }

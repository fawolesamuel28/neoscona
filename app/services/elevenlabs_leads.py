from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.core.tenant import require_tenant
from app.db.supabase import get_supabase
from app.services.leads import get_lead, log_message, upsert_lead

logger = logging.getLogger(__name__)

SOURCE_ELEVENLABS = "elevenlabs_receptionist"


def extract_collection_value(raw: Any) -> str | None:
    """ElevenLabs fields may be plain text or JSON with a `value` key."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return str(raw)
    if not isinstance(raw, str):
        return str(raw).strip() or None

    text = raw.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            data = json.loads(text)
            val = data.get("value")
            if val is not None:
                return str(val).strip() or None
        except json.JSONDecodeError:
            pass
    return text


def normalize_phone(raw: str | None) -> str | None:
    """Normalize to E.164 for Nigeria (+234 + 10-digit NSN)."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw.strip())
    if len(digits) < 10:
        return None

    if digits.startswith("234"):
        national = digits[3:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        national = digits

    if len(national) > 10:
        candidate = national[-10:]
        if candidate[0] not in "789":
            return None
        national = candidate
    if len(national) != 10 or national[0] not in "789":
        return None

    return f"+234{national}"


def resolve_contact_phone(row: dict[str, Any]) -> str | None:
    for field in ("phone_number", "whatsapp_number"):
        parsed = normalize_phone(extract_collection_value(row.get(field)))
        if parsed:
            return parsed
    return None


def normalize_elevenlabs_lead(row: dict[str, Any]) -> dict[str, Any]:
    contact = resolve_contact_phone(row)
    viewed_at = row.get("viewed_at")

    return {
        "id": row["id"],
        "call_id": row.get("call_id"),
        "name": extract_collection_value(row.get("name")),
        "phone_number": normalize_phone(extract_collection_value(row.get("phone_number"))),
        "whatsapp_number": normalize_phone(extract_collection_value(row.get("whatsapp_number"))),
        "contact_phone": contact,
        "budget": extract_collection_value(row.get("budget")),
        "location": extract_collection_value(row.get("location")),
        "property_type": extract_collection_value(row.get("property_type")),
        "timeline": extract_collection_value(row.get("timeline")),
        "ai_summary": extract_collection_value(row.get("ai_summary")),
        "created_at": row.get("created_at"),
        "viewed_at": viewed_at,
        "is_new": viewed_at is None,
        "source": SOURCE_ELEVENLABS,
    }


async def get_all_elevenlabs_leads(tenant_id: str | None = None) -> list[dict[str, Any]]:
    # These helpers run under the service-role client (RLS bypassed), so the explicit
    # tenant filter is the ONLY guard against a cross-tenant read. Require it — never
    # silently return every workspace's voice leads.
    tenant_id = require_tenant(tenant_id)
    try:
        db = get_supabase()

        def _fetch():
            return (
                db.table("elevenlabs_leads")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
                .execute()
            )

        result = await asyncio.to_thread(_fetch)
        return [normalize_elevenlabs_lead(row) for row in result.data]

    except Exception as exc:
        logger.error("Failed to fetch elevenlabs leads: %s", exc)
        return []


async def get_elevenlabs_lead(lead_id: int, tenant_id: str | None = None) -> dict[str, Any] | None:
    tenant_id = require_tenant(tenant_id)
    try:
        db = get_supabase()

        def _fetch():
            return (
                db.table("elevenlabs_leads")
                .select("*")
                .eq("id", lead_id)
                .eq("tenant_id", tenant_id)
                .single()
                .execute()
            )

        result = await asyncio.to_thread(_fetch)
        return normalize_elevenlabs_lead(result.data)

    except Exception as exc:
        logger.error("Failed to fetch elevenlabs lead %s: %s", lead_id, exc)
        return None


async def get_elevenlabs_lead_by_call_id(call_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    """Fetch a voice lead by its ElevenLabs call/conversation id, scoped to tenant.

    Lets the Voice console import a lead using the conversation id it already has
    (the call log and the lead share it), without exposing the integer lead id.
    """
    tenant_id = require_tenant(tenant_id)
    if not call_id:
        return None
    try:
        db = get_supabase()

        def _fetch():
            return (
                db.table("elevenlabs_leads")
                .select("*")
                .eq("call_id", call_id)
                .eq("tenant_id", tenant_id)
                .limit(1)
                .execute()
            )

        result = await asyncio.to_thread(_fetch)
        rows = result.data or []
        return normalize_elevenlabs_lead(rows[0]) if rows else None

    except Exception as exc:
        logger.error("Failed to fetch elevenlabs lead by call_id %s: %s", call_id, exc)
        return None


async def mark_elevenlabs_lead_viewed(lead_id: int, tenant_id: str | None = None) -> dict[str, Any] | None:
    tenant_id = require_tenant(tenant_id)
    try:
        db = get_supabase()
        now = datetime.now(timezone.utc).isoformat()

        def _update():
            return (
                db.table("elevenlabs_leads")
                .update({"viewed_at": now})
                .eq("id", lead_id)
                .eq("tenant_id", tenant_id)
                .select("*")
                .execute()
            )

        result = await asyncio.to_thread(_update)
        if not result.data:
            return None
        return normalize_elevenlabs_lead(result.data[0])

    except Exception as exc:
        logger.error("Failed to mark elevenlabs lead %s viewed: %s", lead_id, exc)
        return None


async def import_elevenlabs_lead_to_pipeline(lead_id: int, tenant_id: str) -> dict[str, Any] | None:
    """Create or update a main `leads` row from a voice receptionist capture.

    `tenant_id` is REQUIRED and must be the resolved owning workspace (from the channel
    registry on the webhook path, or the authenticated principal on the API path) — the
    capture is filed under that tenant, never a hardcoded default.
    """
    tenant_id = require_tenant(tenant_id)
    voice_lead = await get_elevenlabs_lead(lead_id, tenant_id=tenant_id)
    if not voice_lead:
        return None

    phone = voice_lead.get("contact_phone")
    if not phone:
        return None

    existing = await get_lead(phone, tenant_id)
    stage = existing.get("stage", "new") if existing else "new"

    extracted = {
        "name": voice_lead.get("name"),
        "budget": voice_lead.get("budget"),
        "location": voice_lead.get("location"),
        "property_type": voice_lead.get("property_type"),
        "timeline": voice_lead.get("timeline"),
        "source": SOURCE_ELEVENLABS,
        "tenant_id": tenant_id,
    }

    lead = await upsert_lead(phone, extracted, stage=stage, tenant_id=tenant_id)
    if lead:
        summary = voice_lead.get("ai_summary")
        if summary:
            await log_message(
                phone,
                "assistant",
                f"[VOICE_RECEPTIONIST]: {summary}",
                tenant_id=tenant_id,
            )
        await mark_elevenlabs_lead_viewed(lead_id, tenant_id=tenant_id)
    return lead


async def upsert_voice_lead(tenant_id: str, call_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    """Insert (or update on retry) a voice capture from a post-call webhook.

    Scoped to `tenant_id` and idempotent on `(tenant_id, call_id)` — the post-call
    webhook may be retried, so a repeat delivery updates the same row rather than
    duplicating it. Service-role write with an explicit tenant_id (the webhook resolves
    the owning tenant from the channel registry before calling this).
    """
    tenant_id = require_tenant(tenant_id)
    if not call_id:
        logger.warning("upsert_voice_lead called without call_id (tenant=%s); skipping", tenant_id)
        return None
    try:
        db = get_supabase()
        row = {
            "tenant_id": tenant_id,
            "call_id": call_id,
            **{k: v for k, v in fields.items() if v is not None},
        }

        def _upsert():
            return db.table("elevenlabs_leads").upsert(
                row, on_conflict="tenant_id,call_id"
            ).execute()

        res = await asyncio.to_thread(_upsert)
        if not res.data:
            return None
        return normalize_elevenlabs_lead(res.data[0])
    except Exception as exc:
        logger.error("Failed to upsert voice lead (tenant=%s call=%s): %s", tenant_id, call_id, exc)
        return None


async def get_elevenlabs_stats(tenant_id: str | None = None) -> dict[str, int]:
    tenant_id = require_tenant(tenant_id)
    leads = await get_all_elevenlabs_leads(tenant_id=tenant_id)
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).isoformat()

    return {
        "total": len(leads),
        "new": len([l for l in leads if l.get("is_new")]),
        "today": len([l for l in leads if (l.get("created_at") or "") >= today]),
    }

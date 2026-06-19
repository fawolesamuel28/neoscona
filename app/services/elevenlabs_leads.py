from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.core.tenant import get_default_tenant_id
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
    try:
        db = get_supabase()

        def _fetch():
            query = db.table("elevenlabs_leads").select("*")
            if tenant_id:
                query = query.eq("tenant_id", tenant_id)
            return query.order("created_at", desc=True).execute()

        result = await asyncio.to_thread(_fetch)
        return [normalize_elevenlabs_lead(row) for row in result.data]

    except Exception as exc:
        logger.error("Failed to fetch elevenlabs leads: %s", exc)
        return []


async def get_elevenlabs_lead(lead_id: int, tenant_id: str | None = None) -> dict[str, Any] | None:
    try:
        db = get_supabase()

        def _fetch():
            query = db.table("elevenlabs_leads").select("*").eq("id", lead_id)
            if tenant_id:
                query = query.eq("tenant_id", tenant_id)
            return query.single().execute()

        result = await asyncio.to_thread(_fetch)
        return normalize_elevenlabs_lead(result.data)

    except Exception as exc:
        logger.error("Failed to fetch elevenlabs lead %s: %s", lead_id, exc)
        return None


async def mark_elevenlabs_lead_viewed(lead_id: int, tenant_id: str | None = None) -> dict[str, Any] | None:
    try:
        db = get_supabase()
        now = datetime.now(timezone.utc).isoformat()

        def _update():
            query = db.table("elevenlabs_leads").update({"viewed_at": now}).eq("id", lead_id)
            if tenant_id:
                query = query.eq("tenant_id", tenant_id)
            return query.select("*").execute()

        result = await asyncio.to_thread(_update)
        if not result.data:
            return None
        return normalize_elevenlabs_lead(result.data[0])

    except Exception as exc:
        logger.error("Failed to mark elevenlabs lead %s viewed: %s", lead_id, exc)
        return None


async def import_elevenlabs_lead_to_pipeline(lead_id: int) -> dict[str, Any] | None:
    """Create or update a main `leads` row from a voice receptionist capture."""
    voice_lead = await get_elevenlabs_lead(lead_id)
    if not voice_lead:
        return None

    phone = voice_lead.get("contact_phone")
    if not phone:
        return None

    # Voice captures currently land on the system default tenant; Phase 2 resolves
    # the owning tenant from the receptionist/assistant channel registry instead.
    tenant_id = get_default_tenant_id()
    if not tenant_id:
        logger.error("DEFAULT_TENANT_ID is not configured; cannot import voice lead")
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
        await mark_elevenlabs_lead_viewed(lead_id)
    return lead


async def get_elevenlabs_stats(tenant_id: str | None = None) -> dict[str, int]:
    leads = await get_all_elevenlabs_leads(tenant_id=tenant_id)
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).isoformat()

    return {
        "total": len(leads),
        "new": len([l for l in leads if l.get("is_new")]),
        "today": len([l for l in leads if (l.get("created_at") or "") >= today]),
    }

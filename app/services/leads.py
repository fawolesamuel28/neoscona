import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.tenant import apply_tenant_defaults, require_tenant
from app.db.supabase import get_request_client

logger = logging.getLogger(__name__)


def infer_channel_source(phone_number: str) -> str:
    """Best-effort channel label when `leads.source` was never persisted."""
    if not phone_number:
        return "unknown"
    digits = phone_number.lstrip("+")
    if phone_number.startswith("+234") or (digits.isdigit() and len(digits) >= 12):
        return "whatsapp_organic"
    if digits.isdigit():
        return "whatsapp_evolution"
    return "unknown"


async def _fetch_conversation_meta(tenant_id: str) -> dict[str, dict[str, Any]]:
    """Latest activity per phone from conversation_logs (paginated — Supabase caps at 1000/req)."""
    tenant_id = require_tenant(tenant_id)
    try:
        db = get_request_client()
        page_size = 1000
        offset = 0
        all_rows: list[dict[str, Any]] = []

        def _fetch_page(off: int):
            return (
                db.table("conversation_logs")
                .select("phone_number, role, created_at, message")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
                .range(off, off + page_size - 1)
                .execute()
            )

        while True:
            result = await asyncio.to_thread(_fetch_page, offset)
            batch = result.data or []
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        meta: dict[str, dict[str, Any]] = {}
        counts: dict[str, int] = {}
        for row in all_rows:
            phone = row.get("phone_number")
            if not phone:
                continue
            counts[phone] = counts.get(phone, 0) + 1
            if phone not in meta:
                meta[phone] = {
                    "last_message_at": row.get("created_at"),
                    "last_role": row.get("role"),
                    "message_count": 0,
                }
        for phone, count in counts.items():
            if phone in meta:
                meta[phone]["message_count"] = count
        return meta
    except Exception as exc:
        logger.error("Failed to fetch conversation meta: %s", exc)
        return {}


async def upsert_lead(
    phone_number: str,
    extracted_data: dict[str, Any],
    stage: str,
    tenant_id: str | None = None,
) -> dict | None:
    """
    Creates a lead if it's their first time, or updates them if they're returning.
    Only updates fields that have actual values (ignores None/null).

    `tenant_id` is REQUIRED — the inbound path must resolve it (channel registry)
    before writing, so a message is never stored against the wrong workspace.
    """
    tenant_id = require_tenant(tenant_id or extracted_data.get("tenant_id"))
    try:
        db = get_request_client()

        # Fetch existing lead data first — scoped to this tenant so we never merge
        # another workspace's lead that happens to share the phone number.
        def _get_existing():
            return (
                db.table("leads")
                .select("*")
                .eq("phone_number", phone_number)
                .eq("tenant_id", tenant_id)
                .execute()
            )

        existing = await asyncio.to_thread(_get_existing)
        existing_data = existing.data[0] if existing.data else {}

        # Merge — never overwrite existing values with null
        payload = {"phone_number": phone_number, "stage": stage}
        fields = [
            "budget",
            "location",
            "property_type",
            "timeline",
            "language",
            "seriousness_score",
            "assigned_agent_id",
            "name",
            "source",
            "utm_campaign",
            "tenant_id",
            "development_id",
            "attribution",
            "closing_revenue",
            "is_paused",
        ]

        for field in fields:
            new_value = extracted_data.get(field)
            old_value = existing_data.get(field)
            payload[field] = new_value if new_value is not None else old_value

        now = datetime.now(timezone.utc).isoformat()
        if extracted_data.get("first_response_at") and not existing_data.get("first_response_at"):
            payload["first_response_at"] = extracted_data["first_response_at"]
        if stage == "qualified" and not existing_data.get("qualified_at"):
            payload["qualified_at"] = now

        apply_tenant_defaults(payload, tenant_id)

        def _do_upsert():
            return db.table("leads").upsert(
                payload,
                on_conflict="tenant_id,phone_number",
            ).execute()

        result = await asyncio.to_thread(_do_upsert)

        logger.info("Lead upserted: %s | Stage: %s | tenant=%s", phone_number, stage, tenant_id)
        return result.data[0] if result.data else None

    except Exception as exc:
        logger.error("Failed to upsert lead %s: %s", phone_number, exc)
        return None


async def get_lead(phone_number: str, tenant_id: str | None = None) -> dict | None:
    """Fetches a lead's full profile by phone number, scoped to a tenant."""
    tenant_id = require_tenant(tenant_id)
    try:
        db = get_request_client()

        def _get():
            return (
                db.table("leads")
                .select("*")
                .eq("phone_number", phone_number)
                .eq("tenant_id", tenant_id)
                .limit(1)
                .execute()
            )

        result = await asyncio.to_thread(_get)
        if not result.data:
            return None
        return result.data[0]

    except Exception as exc:
        logger.error("Failed to fetch lead %s: %s", phone_number, exc)
        return None


async def log_message(
    phone_number: str,
    role: str,
    message: str,
    *,
    author_user_id: str | None = None,
    tenant_id: str | None = None,
) -> None:
    """
    Logs every message to the conversation_logs table.
    Provides a full audit trail of the conversation for dashboard review.

    `role` is 'user' | 'assistant' | 'human_agent'. `tenant_id` is REQUIRED (the
    inbound path resolves it from the channel; the human-agent path passes the
    dashboard user's org).
    """
    tenant_id = require_tenant(tenant_id)
    try:
        db = get_request_client()

        def _log():
            row = {
                "phone_number": phone_number,
                "role": role,
                "message": message,
                "tenant_id": tenant_id,
            }
            if author_user_id:
                row["author_user_id"] = author_user_id
            return db.table("conversation_logs").insert(row).execute()

        await asyncio.to_thread(_log)

    except Exception as exc:
        logger.error("Failed to log message for %s: %s", phone_number, exc)


async def get_all_leads(stage: str | None = None, tenant_id: str | None = None) -> list:
    """
    Fetches all leads for a tenant, optionally filtered by stage. Merges in any
    phone numbers that have conversation_logs but no leads row (e.g. chat
    IDs when upsert previously failed).
    """
    tenant_id = require_tenant(tenant_id)
    try:
        db = get_request_client()
        conv_meta = await _fetch_conversation_meta(tenant_id=tenant_id)

        def _get_all():
            return (
                db.table("leads")
                .select("*")
                .eq("tenant_id", tenant_id)
                .order("created_at", desc=True)
                .execute()
            )

        result = await asyncio.to_thread(_get_all)
        by_phone: dict[str, dict[str, Any]] = {
            row["phone_number"]: row for row in (result.data or [])
        }

        for phone, meta in conv_meta.items():
            if phone not in by_phone:
                by_phone[phone] = {
                    "phone_number": phone,
                    "stage": "new",
                    "source": infer_channel_source(phone),
                    "name": None,
                    "seriousness_score": 5,
                    "created_at": meta.get("last_message_at"),
                }

        leads = list(by_phone.values())
        for lead in leads:
            phone = lead.get("phone_number")
            if phone and phone in conv_meta:
                lead["last_message_at"] = conv_meta[phone].get("last_message_at")
                lead["message_count"] = conv_meta[phone].get("message_count", 0)
            if not lead.get("source"):
                lead["source"] = infer_channel_source(phone or "")

        leads.sort(
            key=lambda l: l.get("last_message_at") or l.get("created_at") or "",
            reverse=True,
        )

        if stage:
            leads = [l for l in leads if (l.get("stage") or "new") == stage]
        return leads

    except Exception as exc:
        logger.error("Failed to fetch leads: %s", exc)
        return []


async def mark_meeting_booked(phone_number: str, meeting_url: str, tenant_id: str | None = None) -> None:
    """Marks a lead as booked once they schedule through Calendly/similar."""
    tenant_id = require_tenant(tenant_id)
    try:
        db = get_request_client()

        def _update():
            return (
                db.table("leads")
                .update({
                    "meeting_booked": True,
                    "meeting_url": meeting_url,
                    "stage": "done",
                })
                .eq("phone_number", phone_number)
                .eq("tenant_id", tenant_id)
                .execute()
            )

        await asyncio.to_thread(_update)

        logger.info("Meeting booked for %s | tenant=%s", phone_number, tenant_id)

    except Exception as exc:
        logger.error("Failed to mark meeting booked for %s: %s", phone_number, exc)

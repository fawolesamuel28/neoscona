"""Shared inbox + live human takeover.

Promotes the existing pause/resume mechanism into a real handoff loop for dashboard
users (auth `Principal`s, i.e. `memberships` — NOT the WhatsApp `agents` table):

  • takeover/handback — pause/resume the AI and record who/when (`is_paused` already
    gates the pipeline at app/workers/tasks.py step 5).
  • send_human_reply — a human sends a manual message to the lead on its own channel and
    it lands in the same conversation thread, tagged role 'human_agent'.
  • assignment, tags, notes — lightweight CRM affordances.

All operations are tenant-scoped: every read/write filters by `tenant_id` and writes are
guarded by an ownership check (the phone must belong to the caller's tenant).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.auth import Principal
from app.core.dashboard_events import notify_dashboard_update
from app.db.supabase import get_supabase
from app.services.leads import get_lead, log_message
from app.services.messaging import send_outbound_message

logger = logging.getLogger(__name__)

INBOX_FILTERS = ("mine", "unassigned", "takeover", "all")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _update_lead(tenant_id: str, phone: str, updates: dict[str, Any]) -> Optional[dict]:
    """Tenant-scoped targeted update on a lead row (does not touch unrelated fields)."""
    db = get_supabase()

    def _upd():
        return (
            db.table("leads")
            .update(updates)
            .eq("phone_number", phone)
            .eq("tenant_id", tenant_id)
            .execute()
        )

    res = await asyncio.to_thread(_upd)
    return (res.data or [None])[0]


async def _owned_lead(tenant_id: str, phone: str) -> Optional[dict]:
    """Return the lead iff it belongs to this tenant, else None (callers raise 404)."""
    return await get_lead(phone, tenant_id=tenant_id)


# ── Takeover / handback ───────────────────────────────────────────────────────
async def takeover(tenant_id: str, phone: str, principal: Principal) -> Optional[dict]:
    """Pause the AI and mark the lead as taken over by this user."""
    if not await _owned_lead(tenant_id, phone):
        return None
    lead = await _update_lead(tenant_id, phone, {
        "is_paused": True,
        "taken_over_by": principal.user_id,
        "taken_over_at": _now(),
    })
    notify_dashboard_update("pipeline_updated", phone_number=phone)
    return lead


async def handback(tenant_id: str, phone: str) -> Optional[dict]:
    """Resume the AI and clear takeover state."""
    if not await _owned_lead(tenant_id, phone):
        return None
    lead = await _update_lead(tenant_id, phone, {
        "is_paused": False,
        "taken_over_by": None,
        "taken_over_at": None,
        "sla_notified_at": None,
    })
    notify_dashboard_update("pipeline_updated", phone_number=phone)
    return lead


# ── Human reply ───────────────────────────────────────────────────────────────
async def send_human_reply(tenant_id: str, phone: str, principal: Principal, text: str) -> bool:
    """Send a manual reply to the lead on its channel and log it as a human-agent message."""
    lead = await _owned_lead(tenant_id, phone)
    if not lead:
        return False
    source = lead.get("source") or "whatsapp_organic"
    await send_outbound_message(phone, text, source)
    await log_message(
        phone, "human_agent", text,
        author_user_id=principal.user_id, tenant_id=tenant_id,
    )
    # Stamp last-touch so the SLA timer treats this lead as answered.
    await _update_lead(tenant_id, phone, {"sla_notified_at": None})
    notify_dashboard_update("pipeline_updated", phone_number=phone)
    return True


# ── Assignment / tags / notes ─────────────────────────────────────────────────
async def assign(tenant_id: str, phone: str, assignee_user_id: Optional[str]) -> Optional[dict]:
    if not await _owned_lead(tenant_id, phone):
        return None
    lead = await _update_lead(tenant_id, phone, {"assigned_user_id": assignee_user_id})
    notify_dashboard_update("pipeline_updated", phone_number=phone)
    return lead


async def set_tags(tenant_id: str, phone: str, tags: list[str]) -> Optional[dict]:
    if not await _owned_lead(tenant_id, phone):
        return None
    clean = [str(t).strip()[:40] for t in (tags or []) if str(t).strip()][:20]
    lead = await _update_lead(tenant_id, phone, {"tags": clean})
    notify_dashboard_update("pipeline_updated", phone_number=phone)
    return lead


async def add_note(tenant_id: str, phone: str, principal: Principal, body: str) -> Optional[dict]:
    if not await _owned_lead(tenant_id, phone):
        return None
    db = get_supabase()
    row = {
        "tenant_id": tenant_id,
        "lead_phone": phone,
        "author_user_id": principal.user_id,
        "author_email": principal.email,
        "body": body.strip()[:4000],
    }

    def _ins():
        return db.table("lead_notes").insert(row).execute()

    res = await asyncio.to_thread(_ins)
    return (res.data or [row])[0]


async def list_notes(tenant_id: str, phone: str) -> list[dict]:
    db = get_supabase()

    def _get():
        return (
            db.table("lead_notes")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("lead_phone", phone)
            .order("created_at", desc=True)
            .execute()
        )

    res = await asyncio.to_thread(_get)
    return res.data or []


# ── Inbox listing ─────────────────────────────────────────────────────────────
def _sla_overdue(lead: dict, last_inbound_at: Optional[str], sla_minutes: int) -> bool:
    """True when a taken-over lead has an inbound newer than its last human touch + SLA."""
    if not lead.get("is_paused") or not last_inbound_at:
        return False
    try:
        from datetime import datetime as _dt
        inbound = _dt.fromisoformat(last_inbound_at.replace("Z", "+00:00"))
        age_min = (datetime.now(timezone.utc) - inbound).total_seconds() / 60.0
        return age_min >= sla_minutes
    except Exception:
        return False


async def list_inbox(
    tenant_id: str,
    principal: Principal,
    filter_: str = "all",
    sla_minutes: int = 10,
) -> list[dict]:
    """Leads for the inbox, filtered by mine/unassigned/takeover/all with an SLA flag."""
    filter_ = filter_ if filter_ in INBOX_FILTERS else "all"
    db = get_supabase()

    def _get_leads():
        q = db.table("leads").select("*").eq("tenant_id", tenant_id)
        if filter_ == "mine":
            q = q.eq("assigned_user_id", principal.user_id)
        elif filter_ == "unassigned":
            q = q.is_("assigned_user_id", "null")
        elif filter_ == "takeover":
            q = q.eq("is_paused", True)
        return q.order("updated_at", desc=True).execute()

    res = await asyncio.to_thread(_get_leads)
    leads = res.data or []

    # Last inbound (role='user') timestamp per phone, for SLA + preview.
    from app.services.leads import _fetch_conversation_meta
    meta = await _fetch_conversation_meta(tenant_id=tenant_id)

    out = []
    for lead in leads:
        phone = lead.get("phone_number")
        m = meta.get(phone, {})
        last_inbound = m.get("last_message_at") if m.get("last_role") == "user" else None
        out.append({
            **lead,
            "last_message_at": m.get("last_message_at"),
            "last_role": m.get("last_role"),
            "message_count": m.get("message_count", 0),
            "sla_overdue": _sla_overdue(lead, last_inbound, sla_minutes),
        })
    return out

"""Inbox SLA timer (Celery beat).

Finds taken-over leads whose most recent message is an unanswered inbound older than
`INBOX_SLA_MINUTES`, and nudges the team once per breach:
  • emits a dashboard event (the inbox surfaces an "overdue" badge), and
  • best-effort WhatsApp nudge to the lead's assigned WhatsApp agent if one is mapped.
`sla_notified_at` debounces so a single breach pings only once.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.dashboard_events import notify_dashboard_update
from app.db.supabase import get_supabase

logger = logging.getLogger(__name__)


def _sla_minutes() -> int:
    try:
        return int(os.getenv("INBOX_SLA_MINUTES", "10"))
    except ValueError:
        return 10


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


async def _latest_message(phone: str) -> Optional[dict[str, Any]]:
    db = get_supabase()

    def _get():
        return (
            db.table("conversation_logs")
            .select("role, created_at")
            .eq("phone_number", phone)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

    res = await asyncio.to_thread(_get)
    return (res.data or [None])[0]


async def _notify(lead: dict) -> None:
    """Surface the breach on the dashboard and (best-effort) WhatsApp the assigned agent."""
    phone = lead.get("phone_number")
    notify_dashboard_update("pipeline_updated", phone_number=phone)

    agent_id = lead.get("assigned_agent_id")
    if not agent_id:
        return
    try:
        db = get_supabase()

        def _get_agent():
            return db.table("agents").select("*").eq("id", agent_id).limit(1).execute()

        res = await asyncio.to_thread(_get_agent)
        agent = (res.data or [None])[0]
        if agent and agent.get("whatsapp_number"):
            from app.services.messaging import send_outbound_message
            await send_outbound_message(
                agent["whatsapp_number"],
                f"⏰ A lead you're handling is waiting on a reply: wa.me/{phone}. "
                f"Open the inbox to respond.",
            )
    except Exception as exc:
        logger.warning("SLA agent nudge failed for %s: %s", phone, exc)


async def check_inbox_sla() -> int:
    """Scan taken-over leads and notify on fresh SLA breaches. Returns # notified."""
    sla = _sla_minutes()
    notified = 0
    try:
        db = get_supabase()

        def _get_paused():
            return db.table("leads").select("*").eq("is_paused", True).execute()

        res = await asyncio.to_thread(_get_paused)
        leads = res.data or []

        now = datetime.now(timezone.utc)
        for lead in leads:
            phone = lead.get("phone_number")
            if not phone:
                continue
            latest = await _latest_message(phone)
            # Only an unanswered *inbound* counts as waiting.
            if not latest or latest.get("role") != "user":
                continue
            inbound_at = _parse(latest.get("created_at"))
            if not inbound_at:
                continue
            age_min = (now - inbound_at).total_seconds() / 60.0
            if age_min < sla:
                continue
            # Debounce: skip if we already notified for this (or a newer) inbound.
            notified_at = _parse(lead.get("sla_notified_at"))
            if notified_at and notified_at >= inbound_at:
                continue

            await _notify(lead)

            def _stamp(p=phone):
                return (
                    db.table("leads")
                    .update({"sla_notified_at": now.isoformat()})
                    .eq("phone_number", p)
                    .execute()
                )

            await asyncio.to_thread(_stamp)
            notified += 1

        if notified:
            logger.info("Inbox SLA: notified %d overdue lead(s)", notified)
    except Exception as exc:
        logger.error("check_inbox_sla failed: %s", exc)
    return notified

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from app.core.auth import Principal, get_current_principal
from app.db.supabase import get_supabase
from app.services.elevenlabs_leads import get_elevenlabs_stats
from app.services.leads import get_all_leads, get_lead, infer_channel_source
from app.services.inventory import get_lead_matches

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Leads API"])


@router.get("/leads")
async def fetch_leads(
    stage: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
):
    """
    Returns all leads for the caller's tenant, optionally filtered by stage.
    Dashboard calls this on load and every 30 seconds.
    """
    leads = await get_all_leads(stage=stage, tenant_id=principal.tenant_id)
    return {"leads": leads, "total": len(leads)}


@router.get("/leads/{phone_number}")
async def fetch_lead(
    phone_number: str,
    principal: Principal = Depends(get_current_principal),
):
    """
    Returns full profile + conversation log for one lead (scoped to the tenant).
    """
    db = get_supabase()
    tenant_id = principal.tenant_id

    def _get_logs():
        page_size = 1000
        offset = 0
        rows: list = []
        while True:
            query = (
                db.table("conversation_logs")
                .select("*")
                .eq("phone_number", phone_number)
            )
            if tenant_id:
                query = query.eq("tenant_id", tenant_id)
            batch = (
                query.order("created_at")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            data = batch.data or []
            rows.extend(data)
            if len(data) < page_size:
                break
            offset += page_size
        return rows

    logs_data = await asyncio.to_thread(_get_logs)
    if not logs_data:
        return {"error": "Lead not found"}

    lead = await get_lead(phone_number, tenant_id=tenant_id)
    if not lead:
        lead = {
            "phone_number": phone_number,
            "stage": "new",
            "source": infer_channel_source(phone_number),
            "name": None,
            "seriousness_score": 5,
        }

    matches = await get_lead_matches(phone_number, tenant_id=tenant_id)

    return {
        "lead": lead,
        "conversation": logs_data,
        "matched_units": matches,
    }


@router.get("/stats")
async def fetch_stats(principal: Principal = Depends(get_current_principal)):
    """
    Returns KPI numbers for the top summary bar (scoped to the caller's tenant).
    """
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).isoformat()

    leads_list = await get_all_leads(tenant_id=principal.tenant_id)
    data = leads_list

    total = len(data)
    new = len([l for l in data if l.get("stage") == "new"])
    qualifying = len([l for l in data if l.get("stage") == "qualifying"])
    qualified = len([l for l in data if l.get("stage") == "qualified"])
    booked = len([l for l in data if l.get("stage") == "done"])
    today_leads = len([l for l in data if l.get("created_at", "") >= today])

    voice_stats = await get_elevenlabs_stats(tenant_id=principal.tenant_id)

    by_source: dict[str, int] = {}
    for row in data:
        src = row.get("source") or infer_channel_source(row.get("phone_number", ""))
        key = src if src else "unknown"
        by_source[key] = by_source.get(key, 0) + 1

    hot = len([l for l in data if (l.get("seriousness_score") or 0) >= 8])

    return {
        "total": total,
        "new": new,
        "qualifying": qualifying,
        "qualified": qualified,
        "booked": booked,
        "today": today_leads,
        "conversion_rate": round((booked / total * 100), 1) if total > 0 else 0,
        "voice_leads": voice_stats["total"],
        "voice_leads_new": voice_stats["new"],
        "voice_leads_today": voice_stats["today"],
        "hot_leads": hot,
        "by_source": by_source,
    }

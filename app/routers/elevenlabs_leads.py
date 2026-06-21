from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import Principal
from app.core.dashboard_events import notify_dashboard_update
from app.core.entitlements import require_feature
from app.services.elevenlabs_leads import (
    get_all_elevenlabs_leads,
    get_elevenlabs_lead,
    get_elevenlabs_stats,
    import_elevenlabs_lead_to_pipeline,
    mark_elevenlabs_lead_viewed,
)

logger = logging.getLogger(__name__)

# All ElevenLabs lead routes require a valid login and are scoped to the
# caller's tenant (tenant_id added in migration 010, defaults to seeded tenant).
# Voice is a plan feature, so these routes are gated behind `require_feature("voice")`
# (fails open if the plan can't be resolved — see app/core/entitlements.py).
router = APIRouter(tags=["ElevenLabs Leads API"])

# Shared dependency: authenticated principal whose plan includes the voice feature.
require_voice = require_feature("voice")


def _stats_from_leads(leads: list) -> dict[str, int]:
    today_prefix = (
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat()[:10]
    )
    return {
        "total": len(leads),
        "new": sum(1 for lead in leads if lead.get("is_new")),
        "today": sum(
            1 for lead in leads
            if (lead.get("created_at") or "").startswith(today_prefix)
        ),
    }


@router.get("/elevenlabs-leads")
async def fetch_elevenlabs_leads(principal: Principal = Depends(require_voice)):
    """Voice receptionist leads from ElevenLabs calls."""
    leads = await get_all_elevenlabs_leads(tenant_id=principal.tenant_id)
    return {"leads": leads, "total": len(leads), "stats": _stats_from_leads(leads)}


@router.get("/elevenlabs-leads/{lead_id}")
async def fetch_elevenlabs_lead(
    lead_id: int,
    principal: Principal = Depends(require_voice),
):
    lead = await get_elevenlabs_lead(lead_id, tenant_id=principal.tenant_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"lead": lead}


@router.patch("/elevenlabs-leads/{lead_id}/viewed")
async def mark_viewed(
    lead_id: int,
    principal: Principal = Depends(require_voice),
):
    lead = await mark_elevenlabs_lead_viewed(lead_id, tenant_id=principal.tenant_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    notify_dashboard_update("voice_updated", voice_lead_id=lead_id)
    return {"lead": lead}


@router.post("/elevenlabs-leads/{lead_id}/import")
async def import_to_pipeline(
    lead_id: int,
    principal: Principal = Depends(require_voice),
):
    """Push a voice lead into the main WhatsApp sales pipeline."""
    # Ensure the voice lead belongs to the caller's tenant before importing.
    if not await get_elevenlabs_lead(lead_id, tenant_id=principal.tenant_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    lead = await import_elevenlabs_lead_to_pipeline(lead_id, principal.tenant_id)
    if lead:
        phone = lead.get("phone_number")
        notify_dashboard_update("pipeline_updated", phone_number=phone)
        notify_dashboard_update("voice_updated", voice_lead_id=lead_id)
        return {"lead": lead, "message": "Imported to pipeline"}

    voice = await get_elevenlabs_lead(lead_id, tenant_id=principal.tenant_id)
    if not voice:
        raise HTTPException(status_code=404, detail="Lead not found")
    if not voice.get("contact_phone"):
        raise HTTPException(
            status_code=400,
            detail="Lead has no valid phone or WhatsApp number to import",
        )
    raise HTTPException(
        status_code=500,
        detail="Import failed — check server logs (database permissions or RLS)",
    )

"""Voice call log + analytics API for the Neoscona Voice console.

Read-only reporting over `voice_calls` (the full call log) plus two convenience
actions: importing a call's captured lead into the pipeline, and streaming the call
recording. Kept separate from `app/routers/voice.py` (receptionist provisioning) and
`app/routers/elevenlabs_leads.py` (the lead subset) — different shape, same posture.

Every route is gated behind the `voice` plan feature and takes the tenant STRICTLY from
the authenticated principal — never the request. Cross-tenant ids return 404 (not 403)
so a foreign call's existence is never confirmed.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.core.auth import Principal
from app.core.dashboard_events import notify_dashboard_update
from app.core.entitlements import require_feature
from app.services.elevenlabs_leads import (
    get_elevenlabs_lead_by_call_id,
    import_elevenlabs_lead_to_pipeline,
)
from app.services.voice import elevenlabs
from app.services.voice_calls import (
    get_all_calls,
    get_call,
    get_call_analytics,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Voice Calls"])

# Owner/admin with the `voice` plan feature (402 + upgrade hint otherwise).
require_voice = require_feature("voice")

_VALID_STATUS = {"completed", "failed", "no-data", "unknown"}


def _stats(calls: list[dict]) -> dict:
    total = len(calls)
    total_secs = sum(int(c.get("duration_secs") or 0) for c in calls)
    today_prefix = ""
    # `created_at` is an ISO string; "today" compares on the date prefix in UTC.
    from datetime import datetime, timezone

    today_prefix = datetime.now(timezone.utc).date().isoformat()
    return {
        "total_calls": total,
        "total_minutes": round(total_secs / 60.0, 2),
        "avg_duration_secs": round(total_secs / total) if total else 0,
        "today": sum(1 for c in calls if (c.get("created_at") or "").startswith(today_prefix)),
    }


@router.get("/voice/calls")
async def list_calls(
    status: Optional[str] = Query(None),
    period: Optional[str] = Query(None, description="week | month"),
    search: Optional[str] = Query(None, max_length=120),
    principal: Principal = Depends(require_voice),
):
    """The caller's voice calls (newest first) plus summary stats."""
    if status is not None and status not in _VALID_STATUS:
        raise HTTPException(status_code=422, detail="invalid status filter")

    since = None
    if period in ("week", "month"):
        from datetime import datetime, timedelta, timezone

        days = 30 if period == "month" else 7
        since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    calls = await get_all_calls(
        tenant_id=principal.tenant_id, status=status, since=since, search=search
    )
    return {"calls": calls, "total": len(calls), "stats": _stats(calls)}


@router.get("/voice/analytics")
async def voice_analytics(
    period: str = Query("week", description="week | month"),
    principal: Principal = Depends(require_voice),
):
    """Call volume, minutes, by-day series and status breakdown for the overview."""
    return await get_call_analytics(tenant_id=principal.tenant_id, period=period)


@router.get("/voice/calls/{call_id}")
async def get_call_detail(
    call_id: str,
    principal: Principal = Depends(require_voice),
):
    """One call with its full transcript. `call_id` is the UUID PK or conversation_id."""
    call = await get_call(call_id, tenant_id=principal.tenant_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return {"call": call}


@router.post("/voice/calls/{conversation_id}/import")
async def import_call_to_pipeline(
    conversation_id: str,
    principal: Principal = Depends(require_voice),
):
    """Import the lead captured on this call into the unified pipeline.

    Calls and leads share the conversation id (`elevenlabs_leads.call_id`); we resolve
    the lead within the caller's tenant, then reuse the existing import path.
    """
    # Confirm the call belongs to the caller (404 masks cross-tenant + not-found alike).
    if not await get_call(conversation_id, tenant_id=principal.tenant_id):
        raise HTTPException(status_code=404, detail="Call not found")

    lead = await get_elevenlabs_lead_by_call_id(conversation_id, tenant_id=principal.tenant_id)
    if not lead:
        raise HTTPException(status_code=404, detail="No captured lead for this call")

    imported = await import_elevenlabs_lead_to_pipeline(lead["id"], principal.tenant_id)
    if not imported:
        raise HTTPException(
            status_code=400,
            detail="Call has no valid phone or WhatsApp number to import",
        )
    notify_dashboard_update("pipeline_updated", phone_number=imported.get("phone_number"))
    return {"lead": imported, "message": "Imported to pipeline"}


@router.get("/voice/calls/{conversation_id}/recording")
async def get_call_recording(
    conversation_id: str,
    principal: Principal = Depends(require_voice),
):
    """Stream the call recording (MP3) — only if the caller owns the call and it has audio.

    The recording is fetched on demand from ElevenLabs (the webhook stores no URL).
    """
    call = await get_call(conversation_id, tenant_id=principal.tenant_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    if not call.get("has_audio"):
        raise HTTPException(status_code=404, detail="No recording available for this call")
    try:
        audio = await elevenlabs.get_conversation_audio(call["conversation_id"])
    except elevenlabs.ElevenLabsError as exc:
        logger.warning("recording fetch failed for %s: %s", conversation_id, exc)
        raise HTTPException(status_code=502, detail="Could not retrieve recording")
    return Response(content=audio, media_type="audio/mpeg")

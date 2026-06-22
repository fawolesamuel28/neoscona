"""Flutterwave webhook receiver.

POST /webhook/flutterwave

Verifies the `verif-hash` header, rejects stale deliveries, dedups on
`data.id` via `flutterwave_events`, and applies events via billing.apply_flw_event.
Always returns HTTP 200 to avoid retry storms; rejected deliveries return
{"status":"rejected"} body.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.flutterwave import verify_webhook_hash
from app.services.billing import record_flw_event, apply_flw_event

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/flutterwave")
async def flutterwave_webhook(request: Request):
    raw = await request.body()
    header = request.headers.get("verif-hash")
    if not verify_webhook_hash(header):
        logger.warning("Flutterwave webhook rejected: invalid hash")
        return JSONResponse({"status": "rejected"})

    try:
        payload = await request.json()
    except Exception:
        logger.exception("Invalid JSON payload from Flutterwave")
        return JSONResponse({"status": "rejected"})

    event = payload.get("event")
    data = payload.get("data") or {}

    # Replay protection: require a created_at timestamp and reject old deliveries
    created_at = data.get("created_at")
    if created_at:
        try:
            # Accept both ISO with Z and offset-aware strings
            if created_at.endswith("Z"):
                created_at = created_at.replace("Z", "+00:00")
            ts = datetime.fromisoformat(created_at)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - ts > timedelta(minutes=30):
                logger.info("Stale Flutterwave webhook (created_at=%s); rejecting", created_at)
                return JSONResponse({"status": "rejected"})
        except Exception:
            logger.exception("Unable to parse created_at from Flutterwave payload")

    flw_id = data.get("id")
    try:
        ok = await record_flw_event(flw_id, event or "unknown", payload)
        if not ok:
            return JSONResponse({"status": "ok"})
        # Apply but do not propagate errors
        try:
            await apply_flw_event(event or "unknown", data)
        except Exception:
            logger.exception("Error applying Flutterwave event %s", event)
    except Exception:
        logger.exception("Unexpected error handling Flutterwave webhook")

    return JSONResponse({"status": "ok"})

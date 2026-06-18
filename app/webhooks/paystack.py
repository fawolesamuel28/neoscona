"""Paystack webhook — verify signature (HMAC-SHA512), dedup, apply to tenant state.

Mirrors the HMAC pattern in app/webhooks/gateway.py (Paystack uses SHA512 and the
`x-paystack-signature` header). Always returns 200 quickly so Paystack does not retry
on our processing errors; a 401 is returned only for a failed signature.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.services.billing import apply_paystack_event, record_paystack_event
from app.services.paystack import verify_signature

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/paystack")
async def paystack_webhook(request: Request):
    raw = await request.body()
    signature = request.headers.get("x-paystack-signature")

    if not verify_signature(raw, signature):
        logger.warning("Paystack webhook: invalid signature")
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = await request.json()
    except Exception:
        return {"status": "ok"}  # ack malformed bodies; nothing to do

    event_type = payload.get("event", "")
    data = payload.get("data", {}) or {}
    # Prefer a stable per-event id; fall back to the transaction reference.
    paystack_id = str(data.get("id") or data.get("reference") or "") or None

    try:
        is_new = await record_paystack_event(paystack_id, event_type, payload)
        if is_new:
            await apply_paystack_event(event_type, data)
    except Exception as exc:
        logger.error("Paystack webhook processing error (%s): %s", event_type, exc)

    return {"status": "ok"}

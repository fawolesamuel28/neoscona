"""Billing endpoints — subscribe (payments checkout) + billing status."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import Principal, require_role
from app.services.billing import get_billing, start_subscription

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Billing"])


class SubscribeBody(BaseModel):
    plan: str


@router.get("/billing")
async def billing_status(principal: Principal = Depends(require_role("admin"))):
    """Subscription status + trial + usage for the billing panel."""
    return await get_billing(principal.tenant_id)


@router.post("/billing/subscribe")
async def subscribe(
    body: SubscribeBody,
    principal: Principal = Depends(require_role("admin")),
):
    """Start a Flutterwave checkout for the chosen plan; returns a payment link."""
    email = principal.email
    callback = os.getenv("FLUTTERWAVE_CALLBACK_URL")  # e.g. https://app.neoscona.xyz/dashboard
    try:
        result = await start_subscription(principal.tenant_id, body.plan, email, callback)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        logger.error("Payment provider subscribe failed: %s", exc)
        raise HTTPException(status_code=502, detail="Payment provider error")
    return result

"""Billing endpoints — subscribe (payments checkout) + billing status."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import Principal, require_role
from app.services.billing import get_billing, start_subscription
from app.services.flutterwave import initialize_payment
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Billing"])


class SubscribeBody(BaseModel):
    plan: str


class TopUpBody(BaseModel):
    amount_ngn: int = Field(..., description="Amount in whole Naira (₦)")


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


@router.post("/billing/topup")
async def topup(
    body: TopUpBody,
    principal: Principal = Depends(require_role("admin")),
):
    """Start a one-off top-up checkout. Minimum first payment enforced by UI/backend.

    Expects `amount_ngn` as whole Naira (₦). Returns `payment_link` + `tx_ref`.
    """
    tenant_id = principal.tenant_id
    amount = body.amount_ngn
    # Enforce minimum first payment: ₦14,000 (~$10)
    if amount < 14_000:
        raise HTTPException(status_code=400, detail="Minimum top-up is ₦14,000")

    tx_ref = f"neo-topup-{tenant_id}-{os.urandom(4).hex()}-{int(__import__('time').time())}"
    try:
        data = await initialize_payment(email=principal.email, amount_ngn=amount, tx_ref=tx_ref, redirect_url=None, metadata={"tenant_id": tenant_id, "topup": True})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Payment init failed: {exc}")
    return {"payment_link": data.get("link"), "tx_ref": tx_ref}

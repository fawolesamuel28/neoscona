"""Billing endpoints — subscribe (payments checkout) + billing status."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Header, Cookie
from pydantic import BaseModel

from app.core.auth import Principal, require_role, _auth_disabled, _token_from_header_or_cookie, _decode_token, set_request_token, SSO_COOKIE_NAME, _dev_principal, ROLE_HIERARCHY
from app.db.supabase import get_supabase
from app.services.onboarding import membership_tenant_id, provision_workspace
from fastapi import status
import asyncio
from typing import Optional
from app.services.billing import get_billing, start_subscription
from app.services.flutterwave import initialize_payment
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Billing"])


async def ensure_admin_principal(
    authorization: Optional[str] = Header(default=None),
    sso_cookie: Optional[str] = Cookie(default=None, alias=SSO_COOKIE_NAME),
) -> Principal:
    """Ensure the caller is authenticated and has an admin+ membership.

    If the user has no membership, provision a workspace (tenant + owner
    membership) on their behalf and return an owner principal. This is a
    best-effort convenience for freshly-signed-up users; it requires a valid
    Supabase access token (header or SSO cookie).
    """
    if _auth_disabled():
        return _dev_principal()

    token = _token_from_header_or_cookie(authorization, sso_cookie)
    claims = _decode_token(token)
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject")

    # Make downstream code able to create a user-scoped client
    set_request_token(token)

    # Check existing membership
    tenant = await membership_tenant_id(user_id)
    if not tenant:
        # Provision a workspace (creates tenant + owner membership)
        try:
            result = await provision_workspace(user_id, claims.get("email"), "")
            tenant_id = result["tenant"]["id"]
            role = "owner"
        except Exception as exc:
            logger.error("Provisioning failed for user %s: %s", user_id, exc)
            raise HTTPException(status_code=502, detail="Workspace provisioning failed")
    else:
        tenant_id = tenant
        # Resolve role for this tenant
        db = get_supabase()

        def _get_role():
            return db.table("memberships").select("role").eq("user_id", user_id).eq("tenant_id", tenant_id).limit(1).execute()

        res = await asyncio.to_thread(_get_role)
        rows = res.data or []
        role = rows[0]["role"] if rows else "viewer"

    principal = Principal(user_id=user_id, email=claims.get("email"), tenant_id=tenant_id, role=role)
    if not principal.has_role("admin"):
        raise HTTPException(status_code=403, detail="Requires 'admin' role or higher")
    return principal


class SubscribeBody(BaseModel):
    plan: str


class TopUpBody(BaseModel):
    amount_ngn: int = Field(..., description="Amount in whole Naira (₦)")


@router.get("/billing")
async def billing_status(principal: Principal = Depends(ensure_admin_principal)):
    """Subscription status + trial + usage for the billing panel."""
    return await get_billing(principal.tenant_id)


@router.post("/billing/subscribe")
async def subscribe(
    body: SubscribeBody,
    principal: Principal = Depends(ensure_admin_principal),
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
    principal: Principal = Depends(ensure_admin_principal),
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

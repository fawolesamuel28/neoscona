"""Signup / workspace bootstrap / onboarding-progress endpoints."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.billing.plans import public_catalog
from app.core.auth import (
    AuthUser,
    Principal,
    get_authenticated_user,
    get_current_principal,
    require_role,
)
from app.services.channels import (
    KNOWN_PROVIDERS,
    ChannelConflict,
    register_channel,
    verify_channel_ownership,
)
from app.services.onboarding import (
    ONBOARDING_STEPS,
    advance_onboarding,
    build_me,
    provision_workspace,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Onboarding"])


class SignupBody(BaseModel):
    company_name: str = Field(min_length=1, max_length=120)


class OnboardingStepBody(BaseModel):
    step: str


class ChannelBody(BaseModel):
    provider: str = Field(description="cloud | 360dialog | evolution | instagram | vapi | elevenlabs")
    external_id: str = Field(
        min_length=1,
        description="The provider's business-account id (WhatsApp phone_number_id, "
        "Evolution instance, IG account id, Vapi phoneNumberId, …).",
    )
    proof: Optional[str] = Field(
        default=None,
        description="Provider ownership proof (e.g. Meta Embedded-Signup code) used to "
        "verify the caller controls external_id before it is bound to their tenant.",
    )


@router.get("/plans")
async def list_plans():
    """Public NGN plan catalog for the pricing / onboarding UI."""
    return {"plans": public_catalog()}


@router.post("/signup")
async def signup(body: SignupBody, user: AuthUser = Depends(get_authenticated_user)):
    """Provision a workspace (tenant + owner membership + trial) for the signed-in user.

    The Supabase user is created client-side; this is idempotent per user.
    """
    result = await provision_workspace(user.user_id, user.email, body.company_name)
    return {"tenant": result["tenant"], "created": result["created"]}


@router.get("/me")
async def me(principal: Principal = Depends(get_current_principal)):
    """Bootstrap payload: user + tenant + plan + usage + onboarding step."""
    return await build_me(principal.user_id, principal.email, principal.tenant_id, principal.role)


@router.patch("/onboarding")
async def set_onboarding_step(
    body: OnboardingStepBody,
    principal: Principal = Depends(require_role("admin")),
):
    """Advance the onboarding wizard (owner/admin)."""
    if body.step not in ONBOARDING_STEPS:
        raise HTTPException(status_code=422, detail=f"step must be one of {list(ONBOARDING_STEPS)}")
    tenant = await advance_onboarding(principal.tenant_id, body.step)
    return {"tenant": tenant}


@router.post("/channels")
async def connect_channel(
    body: ChannelBody,
    principal: Principal = Depends(require_role("admin")),
):
    """Register a messaging channel for the caller's workspace (owner/admin).

    Binds a business inbox (provider + external_id) to the principal's tenant so the
    webhook gateway can resolve inbound to it. The tenant is taken from the
    authenticated principal — never from the request body — so a caller can only
    register channels for their own workspace.
    """
    if body.provider not in KNOWN_PROVIDERS:
        raise HTTPException(
            status_code=422, detail=f"provider must be one of {sorted(KNOWN_PROVIDERS)}"
        )
    # Prove the caller actually owns this inbox at the provider before binding it —
    # otherwise a tenant could claim another's external_id and capture their inbound.
    owns = await verify_channel_ownership(
        principal.tenant_id, body.provider, body.external_id, body.proof
    )
    if not owns:
        raise HTTPException(
            status_code=403,
            detail="channel ownership could not be verified for this provider",
        )
    try:
        channel = await register_channel(principal.tenant_id, body.provider, body.external_id)
    except ChannelConflict as exc:
        # Already owned by another workspace — don't let one tenant claim another's inbox.
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"channel": channel}

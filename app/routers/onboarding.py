"""Signup / workspace bootstrap / onboarding-progress endpoints."""

from __future__ import annotations

import logging

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

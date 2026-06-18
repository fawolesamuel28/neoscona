"""Self-serve workspace provisioning + onboarding progress.

A new user registers via Supabase Auth (client-side), then calls POST /api/signup,
which creates their tenant + owner membership and starts the trial. Idempotent: a
user who already belongs to an org gets that org back instead of a duplicate.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.billing import plans
from app.core.tenant import get_default_tenant_id
from app.db.supabase import get_supabase
from app.services.usage import get_usage

logger = logging.getLogger(__name__)

# Wizard progression (stored in tenants.onboarding_step).
ONBOARDING_STEPS = ("created", "company", "channel", "inventory", "plan", "live")

_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")

# Tenant columns surfaced to the client (never expose Paystack secrets/customer codes raw).
_TENANT_FIELDS = (
    "id, name, company_name, active, plan, subscription_status, "
    "trial_ends_at, onboarding_step, billing_email, created_at"
)


async def _get_tenant(tenant_id: str) -> dict[str, Any]:
    db = get_supabase()

    def _get():
        return db.table("tenants").select(_TENANT_FIELDS).eq("id", tenant_id).limit(1).execute()

    res = await asyncio.to_thread(_get)
    return res.data[0] if res.data else {}


async def membership_tenant_id(user_id: str) -> Optional[str]:
    """Highest-privilege org the user already belongs to, or None."""
    if not _UUID_RE.match(user_id or ""):
        return None
    db = get_supabase()

    def _get():
        return db.table("memberships").select("tenant_id, role").eq("user_id", user_id).execute()

    res = await asyncio.to_thread(_get)
    rows = res.data or []
    if not rows:
        return None
    from app.core.auth import ROLE_HIERARCHY

    best = max(rows, key=lambda r: ROLE_HIERARCHY.get(r.get("role"), -1))
    return best["tenant_id"]


async def provision_workspace(user_id: str, email: Optional[str], company_name: str) -> dict[str, Any]:
    """Create tenant + owner membership + trial for a user. Idempotent per user."""
    # Dev bypass user is not a real auth.users row — hand back the default tenant.
    if not _UUID_RE.match(user_id or ""):
        return {"tenant": await _get_tenant(get_default_tenant_id()), "created": False}

    existing = await membership_tenant_id(user_id)
    if existing:
        return {"tenant": await _get_tenant(existing), "created": False}

    name = (company_name or "My Workspace").strip()[:120] or "My Workspace"
    trial_ends = (datetime.now(timezone.utc) + timedelta(days=plans.trial_days())).isoformat()
    db = get_supabase()

    def _create():
        t = db.table("tenants").insert({
            "name": name,
            "company_name": name,
            "active": True,
            "plan": "trial",
            "subscription_status": "trialing",
            "trial_ends_at": trial_ends,
            "billing_email": email,
            "onboarding_step": "company",
        }).execute()
        tenant = t.data[0]
        db.table("memberships").insert({
            "user_id": user_id,
            "tenant_id": tenant["id"],
            "role": "owner",
        }).execute()
        return tenant

    try:
        tenant = await asyncio.to_thread(_create)
        logger.info("Provisioned workspace %s for user %s", tenant.get("id"), user_id)
        return {"tenant": tenant, "created": True}
    except Exception as exc:
        # Possible race: another request created it first. Re-resolve.
        logger.warning("provision_workspace insert failed, re-resolving: %s", exc)
        again = await membership_tenant_id(user_id)
        if again:
            return {"tenant": await _get_tenant(again), "created": False}
        raise


async def advance_onboarding(tenant_id: str, step: str) -> dict[str, Any]:
    """Set the onboarding step (validated against ONBOARDING_STEPS)."""
    if step not in ONBOARDING_STEPS:
        raise ValueError(f"invalid onboarding step: {step}")
    db = get_supabase()

    def _update():
        return (
            db.table("tenants")
            .update({"onboarding_step": step, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", tenant_id)
            .execute()
        )

    await asyncio.to_thread(_update)
    return await _get_tenant(tenant_id)


async def build_me(user_id: str, email: Optional[str], tenant_id: str, role: str) -> dict[str, Any]:
    """Single bootstrap payload for the app: user + tenant + plan + usage."""
    tenant = await _get_tenant(tenant_id)
    plan_key = tenant.get("plan") or "trial"
    usage = await get_usage(tenant_id, plan=plan_key)
    plan_cfg = plans.get_plan(plan_key)
    return {
        "user": {"id": user_id, "email": email, "role": role},
        "tenant": tenant,
        "plan": {
            "id": plan_key,
            "label": plan_cfg["label"],
            "features": sorted(plan_cfg["features"]),
            "limits": plan_cfg["limits"],
        },
        "usage": usage,
        "onboarding_step": tenant.get("onboarding_step"),
    }

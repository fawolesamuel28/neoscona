"""Plan entitlement enforcement (Phase 2b).

Two enforcement surfaces sit on top of the plan catalog + usage metering:

  • HTTP feature gates — `require_feature(...)` is a FastAPI dependency that
    rejects a route (402 + upgrade hint) when the caller's plan lacks a feature.
  • Message-pipeline reply gate — `reply_allowed(tenant_id)` decides whether the
    AI may respond, given the tenant's subscription status and message quota.

Philosophy mirrors `app/services/usage.py`: enforcement is **soft by default**.
A `canceled` subscription is the one unambiguous hard stop (the account is off).
Quota overage only blocks when `ENFORCE_HARD_LIMITS` is set, so turning real
enforcement on is a deliberate, reversible operator toggle — not a silent change
that takes live tenants offline. Every lookup **fails open**: an enforcement bug
must never block a paying tenant's leads.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import NamedTuple, Optional

from fastapi import Depends, HTTPException

from app.billing.plans import feature_enabled, get_plan
from app.core.auth import Principal, get_current_principal
from app.core.metrics import ENTITLEMENT_BLOCKS
from app.db.supabase import get_supabase
from app.services.usage import over_limit

logger = logging.getLogger(__name__)


def hard_limits_enabled() -> bool:
    """When false (default), quota overage is metered (see usage.py) but never blocks.

    Controlled by the `BILLING_ENFORCEMENT` env knob ('soft' = meter only, the
    default; 'hard'/'strict'/'on' = block over-quota replies).
    """
    return (os.getenv("BILLING_ENFORCEMENT") or "soft").lower() in ("hard", "strict", "enforce", "on", "true")


async def _tenant_billing(tenant_id: str) -> tuple[str, str]:
    """Return (plan, subscription_status) for a tenant. Fails open to ('trial', 'active').

    Fail-open also covers the pre-migration window where the `plan` column does not
    yet exist — the select errors, we log, and enforcement stays out of the way.
    """
    try:
        db = get_supabase()

        def _get():
            return (
                db.table("tenants")
                .select("plan, subscription_status")
                .eq("id", tenant_id)
                .limit(1)
                .execute()
            )

        res = await asyncio.to_thread(_get)
        row = (res.data or [{}])[0] if res.data else {}
        return (row.get("plan") or "trial", row.get("subscription_status") or "active")
    except Exception as exc:
        logger.warning("entitlement plan lookup failed for %s (fail-open): %s", tenant_id, exc)
        return ("trial", "active")


# ── HTTP feature gate ─────────────────────────────────────────────────────────
def require_feature(feature: str):
    """Dependency factory: 402 when the caller's plan does not include `feature`.

    Feature gates are product boundaries (the plan genuinely lacks the capability),
    so they apply regardless of the soft `ENFORCE_HARD_LIMITS` quota toggle.
    """

    async def _dep(principal: Principal = Depends(get_current_principal)) -> Principal:
        plan, _status = await _tenant_billing(principal.tenant_id)
        if not feature_enabled(plan, feature):
            ENTITLEMENT_BLOCKS.labels(reason=f"feature:{feature}").inc()
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "plan_upgrade_required",
                    "feature": feature,
                    "plan": plan,
                    "message": (
                        f"Your {get_plan(plan)['label']} plan does not include "
                        f"'{feature}'. Upgrade to enable it."
                    ),
                },
            )
        return principal

    return _dep


# ── Message-pipeline reply gate ───────────────────────────────────────────────
class ReplyDecision(NamedTuple):
    allowed: bool
    reason: Optional[str] = None


async def reply_allowed(tenant_id: Optional[str]) -> ReplyDecision:
    """Whether the AI may reply for this tenant. Soft by default; always fails open.

    Blocks when the subscription is `canceled` (hard stop). Blocks on message-quota
    overage only when `ENFORCE_HARD_LIMITS` is set; otherwise overage is recorded by
    metering but the reply still goes out.
    """
    if not tenant_id:
        return ReplyDecision(True)
    try:
        plan, status = await _tenant_billing(tenant_id)
        if status == "canceled":
            ENTITLEMENT_BLOCKS.labels(reason="subscription_canceled").inc()
            return ReplyDecision(False, "subscription_canceled")
        if hard_limits_enabled() and await over_limit(tenant_id, "messages", plan=plan):
            ENTITLEMENT_BLOCKS.labels(reason="message_quota_exceeded").inc()
            return ReplyDecision(False, "message_quota_exceeded")
        return ReplyDecision(True)
    except Exception as exc:
        logger.warning("reply_allowed failed for %s (fail-open): %s", tenant_id, exc)
        return ReplyDecision(True)

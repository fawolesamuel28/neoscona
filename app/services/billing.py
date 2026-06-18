"""Billing state transitions — bridges Paystack events to tenant subscription state.

Onboarding-first: start a subscription checkout and react to webhooks
(charge.success → activate; subscription.disable → past_due). Idempotency is
enforced via the paystack_events table (unique paystack_id).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.billing.plans import get_plan, paystack_plan_code
from app.db.supabase import get_supabase
from app.services.paystack import initialize_transaction
from app.services.usage import get_usage

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def start_subscription(
    tenant_id: str, plan: str, email: str, callback_url: Optional[str] = None
) -> dict[str, Any]:
    """Initialize a Paystack checkout for a selectable plan. Returns the auth URL."""
    cfg = get_plan(plan)
    if not cfg.get("selectable") or cfg.get("price_kobo") is None:
        raise ValueError(f"plan '{plan}' is not self-serve purchasable")
    if not email:
        raise ValueError("a billing email is required to subscribe")

    data = await initialize_transaction(
        email=email,
        amount_kobo=cfg["price_kobo"],
        plan_code=paystack_plan_code(plan),
        callback_url=callback_url,
        metadata={"tenant_id": tenant_id, "plan": plan},
    )
    return {"authorization_url": data["authorization_url"], "reference": data["reference"]}


async def get_billing(tenant_id: str) -> dict[str, Any]:
    """Subscription status + trial + current usage for the billing panel."""
    db = get_supabase()

    def _get():
        return (
            db.table("tenants")
            .select("plan, subscription_status, trial_ends_at, billing_email, "
                    "paystack_subscription_code")
            .eq("id", tenant_id)
            .limit(1)
            .execute()
        )

    res = await asyncio.to_thread(_get)
    tenant = res.data[0] if res.data else {}
    usage = await get_usage(tenant_id, plan=tenant.get("plan"))
    return {"billing": tenant, "usage": usage}


# ── Webhook-driven state ──────────────────────────────────────────────────────
async def record_paystack_event(paystack_id: Optional[str], event_type: str, payload: dict) -> bool:
    """Insert the event for idempotency/audit. Returns False if already processed."""
    if not paystack_id:
        return True  # nothing to dedup on; let the handler run
    db = get_supabase()

    def _ins():
        return db.table("paystack_events").insert({
            "paystack_id": paystack_id,
            "event_type": event_type,
            "payload": payload,
        }).execute()

    try:
        await asyncio.to_thread(_ins)
        return True
    except Exception:
        # Unique violation on paystack_id → duplicate delivery.
        logger.info("Paystack event %s already processed; skipping", paystack_id)
        return False


async def _update_tenant(match_col: str, match_val: str, updates: dict) -> None:
    db = get_supabase()
    updates = {**updates, "updated_at": _now()}

    def _upd():
        return db.table("tenants").update(updates).eq(match_col, match_val).execute()

    await asyncio.to_thread(_upd)


async def apply_paystack_event(event_type: str, data: dict) -> None:
    """Map a verified Paystack event to a tenant subscription change."""
    if event_type in ("charge.success", "subscription.create"):
        meta = data.get("metadata") or {}
        tenant_id = meta.get("tenant_id")
        plan = meta.get("plan")
        customer = (data.get("customer") or {}).get("customer_code")
        sub_code = data.get("subscription_code")
        if tenant_id and plan:
            updates = {"plan": plan, "subscription_status": "active"}
            if customer:
                updates["paystack_customer_code"] = customer
            if sub_code:
                updates["paystack_subscription_code"] = sub_code
            await _update_tenant("id", tenant_id, updates)
            logger.info("Activated subscription for tenant %s (%s)", tenant_id, plan)
        else:
            logger.warning("charge.success without tenant metadata; skipping activation")

    elif event_type in ("subscription.disable", "subscription.not_renew"):
        sub_code = data.get("subscription_code")
        email = (data.get("customer") or {}).get("email")
        status = "canceled" if event_type == "subscription.disable" else "past_due"
        if sub_code:
            await _update_tenant("paystack_subscription_code", sub_code, {"subscription_status": status})
        elif email:
            await _update_tenant("billing_email", email, {"subscription_status": status})

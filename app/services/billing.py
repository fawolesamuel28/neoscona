"""Billing state transitions — bridges Flutterwave events to tenant subscription state.

This module initializes Flutterwave checkouts and applies webhook-driven state
changes. Idempotency is enforced via the `flutterwave_events` table (unique flw_id).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from uuid import uuid4

from app.billing.plans import get_plan, plan_amount_ngn
from app.core.tenant import require_tenant
from app.db.supabase import get_supabase
from app.services.flutterwave import initialize_payment, tokenized_charge
from app.services.usage import get_usage

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def start_subscription(
    tenant_id: str, plan: str, email: str, callback_url: Optional[str] = None
) -> dict[str, Any]:
    """Initialize a Flutterwave checkout for a selectable plan. Returns link + tx_ref."""
    tenant_id = require_tenant(tenant_id)
    cfg = get_plan(plan)
    amount_ngn = plan_amount_ngn(plan)
    if not cfg.get("selectable") or amount_ngn is None:
        raise ValueError(f"plan '{plan}' is not self-serve purchasable")
    if not email:
        raise ValueError("a billing email is required to subscribe")

    tx_ref = f"neo-{tenant_id}-{uuid4().hex[:8]}-{int(datetime.now(timezone.utc).timestamp())}"
    data = await initialize_payment(
        email=email,
        amount_ngn=amount_ngn,
        tx_ref=tx_ref,
        redirect_url=callback_url,
        metadata={"tenant_id": tenant_id, "plan": plan},
    )
    # Flutterwave returns a hosted link under `link` in the data object
    return {"payment_link": data.get("link"), "tx_ref": tx_ref}


async def get_billing(tenant_id: str) -> dict[str, Any]:
    """Subscription status + trial + current usage for the billing panel."""
    tenant_id = require_tenant(tenant_id)
    db = get_supabase()

    def _get():
        return (
            db.table("tenants")
            .select(
                "plan, subscription_status, trial_ends_at, billing_email, balance, "
                "flw_customer_id, flw_tx_ref, flw_card_token, flw_token_email, next_billing_date"
            )
            .eq("id", tenant_id)
            .limit(1)
            .execute()
        )

    res = await asyncio.to_thread(_get)
    tenant = res.data[0] if res.data else {}
    usage = await get_usage(tenant_id, plan=tenant.get("plan"))
    return {"billing": tenant, "usage": usage}


# ── Webhook-driven state ──────────────────────────────────────────────────────
async def record_flw_event(flw_id: Optional[str], event_type: str, payload: dict) -> bool:
    """Insert the event for idempotency/audit. Returns False if already processed."""
    if not flw_id:
        return True
    db = get_supabase()

    def _ins():
        return db.table("flutterwave_events").insert({
            "flw_id": flw_id,
            "event_type": event_type,
            "payload": payload,
        }).execute()

    try:
        await asyncio.to_thread(_ins)
        return True
    except Exception:
        logger.info("Flutterwave event %s already processed; skipping", flw_id)
        return False


async def _update_tenant(match_col: str, match_val: str, updates: dict) -> None:
    db = get_supabase()
    updates = {**updates, "updated_at": _now()}

    def _upd():
        return db.table("tenants").update(updates).eq(match_col, match_val).execute()

    await asyncio.to_thread(_upd)


async def _credit_balance(tenant_id: str, amount: float) -> None:
    db = get_supabase()
    def _upd():
        return db.rpc("credit_balance", {"p_tenant": tenant_id, "p_amount": amount}).execute()
    await asyncio.to_thread(_upd)

async def apply_flw_event(event_type: str, data: dict) -> None:
    """Map a verified Flutterwave event to a tenant subscription change."""
    # Example: event_type == 'charge.completed'
    if event_type == "charge.completed":
        status = data.get("status")
        meta = data.get("meta") or {}
        tenant_id = meta.get("tenant_id")
        plan = meta.get("plan")
        card_token = (data.get("card") or {}).get("token")
        email = (data.get("customer") or {}).get("email")
        flw_customer = (data.get("customer") or {}).get("id") or data.get("customer_id")
        flw_tx_ref = data.get("tx_ref") or data.get("reference")

        if status == "successful" and tenant_id and plan:
            updates: dict[str, Any] = {"plan": plan, "subscription_status": "active"}
            if flw_customer:
                updates["flw_customer_id"] = flw_customer
            if flw_tx_ref:
                updates["flw_tx_ref"] = flw_tx_ref
            if card_token:
                updates["flw_card_token"] = card_token
            if email:
                updates["flw_token_email"] = email
            await _update_tenant("id", tenant_id, updates)
            logger.info("Activated subscription for tenant %s (%s) via Flutterwave", tenant_id, plan)
        elif status == "successful" and tenant_id and meta.get("topup"):
            amount = float(data.get("amount", 0.0))
            await _credit_balance(tenant_id, amount)
            logger.info("Credited NGN %s to tenant %s via Flutterwave top-up", amount, tenant_id)
        else:
            logger.warning("Flutterwave charge not successful or missing metadata: %s", data)

    elif event_type in ("subscription.disable", "subscription.not_renew"):
        # Map to tenant cancellation / past_due
        status = "canceled" if event_type == "subscription.disable" else "past_due"
        sub_ref = data.get("tx_ref") or data.get("reference")
        email = (data.get("customer") or {}).get("email")
        if sub_ref:
            await _update_tenant("flw_tx_ref", sub_ref, {"subscription_status": status})
        elif email:
            await _update_tenant("billing_email", email, {"subscription_status": status})


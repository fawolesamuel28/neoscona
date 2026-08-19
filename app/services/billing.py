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

from app.billing.plans import get_plan, plan_amount_ngn, feature_enabled
from app.core.tenant import require_tenant
from app.db.supabase import get_supabase
from app.services import ledger
from app.services.flutterwave import (
    initialize_payment,
    tokenized_charge,
    verify_transaction_by_reference,
)
from app.services.usage import get_usage

logger = logging.getLogger(__name__)

# How long a checkout-initiated payment can sit unresolved before reconciliation
# starts polling Flutterwave for it, and how long before it's given up on.
PENDING_RECONCILE_AFTER = timedelta(minutes=15)
PENDING_EXPIRE_AFTER = timedelta(hours=24)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enqueue_voice_provisioning(tenant_id: str) -> None:
    """Fire-and-forget: enqueue receptionist provisioning for a tenant whose plan
    includes the `voice` feature. Never raises — a webhook handler must stay fast
    and must not fail the payment flow over a Celery/Redis hiccup."""
    try:
        from app.workers.celery_app import celery_app

        celery_app.send_task("auto_provision_voice_task", args=[tenant_id])
    except Exception as exc:
        logger.warning("Failed to enqueue voice auto-provisioning for tenant %s: %s", tenant_id, exc)


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

    # Passing the current plan into metadata ensures we safely issue proration
    # credits ONLY if the checkout completes successfully.
    current = await get_billing(tenant_id)
    current_plan = current["billing"].get("plan")
    meta = {"tenant_id": tenant_id, "plan": plan}
    if current_plan and current_plan != plan and current_plan != "trial":
        meta["prorate_from"] = current_plan
        
    tx_ref = f"sub-{tenant_id}-{uuid4().hex[:8]}"
    data = await initialize_payment(
        email=email,
        amount_ngn=amount_ngn,
        tx_ref=tx_ref,
        redirect_url=callback_url,
        metadata=meta,
    )
    await create_pending_transaction(tenant_id, amount_ngn, "subscription", tx_ref, metadata=meta)
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

    def _history():
        return (
            db.table("billing_transactions")
            .select("id, amount, currency, type, status, description, created_at")
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )

    res = await asyncio.to_thread(_get)
    history = await asyncio.to_thread(_history)
    tenant = res.data[0] if res.data else {}
    usage = await get_usage(tenant_id, plan=tenant.get("plan"))
    return {
        "billing": tenant,
        "usage": usage,
        "transactions": history.data if history.data else []
    }


async def add_transaction(
    tenant_id: str,
    amount: float,
    tx_type: str,
    status: str = "successful",
    currency: str = "NGN",
    flw_ref: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[dict] = None
) -> str:
    """Record a billing event in the ledger."""
    db = get_supabase()
    payload = {
        "tenant_id": tenant_id,
        "amount": amount,
        "type": tx_type,
        "status": status,
        "currency": currency,
        "flw_ref": flw_ref,
        "description": description,
        "metadata": metadata or {},
    }

    def _ins():
        return db.table("billing_transactions").insert(payload).execute()

    res = await asyncio.to_thread(_ins)
    return res.data[0]["id"] if res.data else ""


async def create_pending_transaction(
    tenant_id: str, amount: float, tx_type: str, tx_ref: str, metadata: Optional[dict] = None
) -> str:
    """Record a checkout-initiated payment before its webhook arrives.

    This is what makes reconciliation possible: without a `pending` row to
    resolve, there's nothing for `verify_pending_transaction` to check.
    """
    return await add_transaction(
        tenant_id=tenant_id,
        amount=amount,
        tx_type=tx_type,
        status="pending",
        flw_ref=tx_ref,
        description=f"Awaiting Flutterwave confirmation ({tx_type})",
        metadata=metadata,
    )


async def _resolve_transaction(
    tx_ref: Optional[str],
    *,
    tenant_id: str,
    amount: float,
    tx_type: str,
    status: str,
    currency: str = "NGN",
    description: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Resolve the pending transaction for `tx_ref` to `status`, or insert a new
    row if none was pre-registered (e.g. events with no matching checkout)."""
    db = get_supabase()

    if tx_ref:
        def _find():
            return (
                db.table("billing_transactions")
                .select("id")
                .eq("flw_ref", tx_ref)
                .eq("status", "pending")
                .limit(1)
                .execute()
            )

        res = await asyncio.to_thread(_find)
        if res.data:
            row_id = res.data[0]["id"]
            updates = {
                "status": status,
                "amount": amount,
                "currency": currency,
                "description": description,
                "metadata": metadata or {},
                "updated_at": _now(),
            }

            def _upd():
                return db.table("billing_transactions").update(updates).eq("id", row_id).execute()

            await asyncio.to_thread(_upd)
            return row_id

    return await add_transaction(
        tenant_id=tenant_id,
        amount=amount,
        tx_type=tx_type,
        status=status,
        currency=currency,
        flw_ref=tx_ref,
        description=description,
        metadata=metadata,
    )


# Receipts already exist: GET /billing/invoice/{tx_id} (server.py) renders a
# billing_transactions row via templates/invoice.html, linked from billing.html's
# transaction table for every 'successful' row — no separate invoice record needed.


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


async def update_tenant_billing_fields(tenant_id: str, updates: dict) -> None:
    """Public entry point for other billing-adjacent modules (e.g. dunning) to
    update tenant billing/subscription columns without reaching into the
    module-private `_update_tenant`."""
    await _update_tenant("id", tenant_id, updates)


async def _update_tenant(match_col: str, match_val: str, updates: dict) -> None:
    db = get_supabase()
    updates = {**updates, "updated_at": _now()}

    def _upd():
        return db.table("tenants").update(updates).eq(match_col, match_val).execute()

    await asyncio.to_thread(_upd)


async def apply_flw_event(event_type: str, data: dict) -> None:
    """Map a verified Flutterwave event to a tenant subscription change."""
    if event_type == "charge.completed":
        status = data.get("status")
        meta = data.get("meta") or {}
        tenant_id = meta.get("tenant_id")
        plan = meta.get("plan")
        amount = data.get("amount")
        currency = data.get("currency", "NGN")
        card_token = (data.get("card") or {}).get("token")
        email = (data.get("customer") or {}).get("email")
        flw_customer = (data.get("customer") or {}).get("id") or data.get("customer_id")
        flw_tx_ref = data.get("tx_ref") or data.get("reference")
        flw_id = data.get("id")

        if status == "successful" and tenant_id:
            # 1. Update tenant state
            updates: dict[str, Any] = {"subscription_status": "active"}
            if plan:
                updates["plan"] = plan
            if flw_customer:
                updates["flw_customer_id"] = flw_customer
            if flw_tx_ref:
                updates["flw_tx_ref"] = flw_tx_ref
            if card_token:
                updates["flw_card_token"] = card_token
            if email:
                updates["flw_token_email"] = email
            
            # Proration: issue credit for the previous plan if this was an upgrade/switch
            prorate_from = meta.get("prorate_from")
            if prorate_from:
                await apply_proration(tenant_id, prorate_from)
            
            # If it's a top-up or a renewal, we might want to advance the next_billing_date
            # For simplicity, we assume monthly for now
            next_date = datetime.now(timezone.utc) + timedelta(days=30)
            updates["next_billing_date"] = next_date.isoformat()
            
            await _update_tenant("id", tenant_id, updates)

            # 2. Resolve the pending transaction (or insert one if none was pre-registered)
            tx_type = "subscription" if plan else "topup"
            tx_id = await _resolve_transaction(
                flw_tx_ref,
                tenant_id=tenant_id,
                amount=amount,
                tx_type=tx_type,
                status="successful",
                currency=currency,
                description=f"Flutterwave {plan or 'Balance'} Payment",
            )

            # 3. Update Balance (if it was a manual topup not tied to a specific plan)
            if not plan:
                await ledger.credit(
                    tenant_id, amount, "topup", ref_type="billing_transaction", ref_id=tx_id
                )

            # 4. Successful subscription payment on a voice-enabled plan auto-provisions
            #    a receptionist (idempotent — safe on renewals too, not just first payment).
            if plan and feature_enabled(plan, "voice"):
                _enqueue_voice_provisioning(tenant_id)

            logger.info("Processed successful charge for tenant %s via Flutterwave", tenant_id)
        else:
            if tenant_id:
                await _resolve_transaction(
                    flw_tx_ref,
                    tenant_id=tenant_id,
                    amount=amount or 0,
                    tx_type="subscription",
                    status="failed",
                    description=f"Failed transaction: {data.get('processor_response', 'Unknown error')}",
                )
            logger.warning("Flutterwave charge failed or missing metadata: %s", data)

    elif event_type in ("subscription.disable", "subscription.not_renew"):
        status = "canceled" if event_type == "subscription.disable" else "past_due"
        sub_ref = data.get("tx_ref") or data.get("reference")
        email = (data.get("customer") or {}).get("email")
        if sub_ref:
            await _update_tenant("flw_tx_ref", sub_ref, {"subscription_status": status})
        elif email:
            await _update_tenant("billing_email", email, {"subscription_status": status})


async def process_automated_renewals() -> None:
    """Scan for active tenants whose billing date has passed and charge their saved tokens."""
    db = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    
    def _get_to_bill():
        return (
            db.table("tenants")
            .select("id, plan, billing_email, flw_card_token, flw_token_email")
            .eq("subscription_status", "active")
            .lte("next_billing_date", now)
            .not_.is_("flw_card_token", "null")
            .execute()
        )
    
    res = await asyncio.to_thread(_get_to_bill)
    tenants = res.data or []
    
    for t in tenants:
        tenant_id = t["id"]
        plan = t["plan"]
        token = t["flw_card_token"]
        email = t["flw_token_email"] or t["billing_email"]
        amount = plan_amount_ngn(plan)
        
        if not amount:
            continue
            
        tx_ref = f"renew-{tenant_id}-{uuid4().hex[:6]}-{int(datetime.now(timezone.utc).timestamp())}"
        
        try:
            charge = await tokenized_charge(token, email, amount, tx_ref)
            if charge.get("status") == "successful":
                # Success - advance billing date
                next_date = datetime.now(timezone.utc) + timedelta(days=30)
                await _update_tenant("id", tenant_id, {
                    "next_billing_date": next_date.isoformat(),
                    "subscription_status": "active"
                })
                await add_transaction(
                    tenant_id=tenant_id,
                    amount=amount,
                    tx_type="subscription",
                    status="successful",
                    flw_ref=tx_ref,
                    description=f"Auto-renewal for {plan} plan"
                )
            else:
                # Failed - mark past_due and hand off to the staged dunning flow
                # (dunning.process_dunning drives the day+3/+7/+10 retry/cancel schedule).
                await _update_tenant("id", tenant_id, {
                    "subscription_status": "past_due",
                    "dunning_stage": 1,
                    "dunning_next_action_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
                })

                failure_reason = charge.get('processor_response', 'Unknown error')
                await add_transaction(
                    tenant_id=tenant_id,
                    amount=amount,
                    tx_type="subscription",
                    status="failed",
                    flw_ref=tx_ref,
                    description=f"Auto-renewal failed: {failure_reason}"
                )

                # Immediate first-stage dunning notice
                from app.services.dunning import notify_billing_failure
                await notify_billing_failure(tenant_id, amount, failure_reason)

        except Exception as e:
            logger.error("Failed to process renewal for tenant %s: %s", tenant_id, e)


async def verify_pending_transaction(tx_ref: str) -> Optional[str]:
    """Pull transaction status from Flutterwave for a checkout that never got a
    webhook, and resolve it via the same state-transition path as the webhook.

    Returns the resolved status ('successful'/'failed') or None if Flutterwave
    has no record of it yet (still genuinely in-flight).
    """
    try:
        data = await verify_transaction_by_reference(tx_ref)
    except Exception as exc:
        logger.info("verify_pending_transaction: no resolution yet for %s: %s", tx_ref, exc)
        return None

    status = data.get("status")
    if status not in ("successful", "failed"):
        return None

    await apply_flw_event("charge.completed", data)
    return status


async def reconcile_pending_transactions() -> None:
    """Sweep `pending` billing_transactions and resolve the ones a webhook missed.

    Rows younger than PENDING_RECONCILE_AFTER are left alone (the webhook is
    still the fast path); rows older than PENDING_EXPIRE_AFTER with no
    resolution from Flutterwave are marked 'expired' so they stop being polled.
    """
    db = get_supabase()
    now = datetime.now(timezone.utc)
    cutoff = (now - PENDING_RECONCILE_AFTER).isoformat()

    def _get_pending():
        return (
            db.table("billing_transactions")
            .select("id, tenant_id, flw_ref, created_at")
            .eq("status", "pending")
            .lte("created_at", cutoff)
            .execute()
        )

    res = await asyncio.to_thread(_get_pending)
    rows = res.data or []
    for row in rows:
        tx_ref = row.get("flw_ref")
        if not tx_ref:
            continue
        resolved = await verify_pending_transaction(tx_ref)
        if resolved is None:
            created_at = row.get("created_at")
            try:
                age = now - datetime.fromisoformat((created_at or "").replace("Z", "+00:00"))
            except Exception:
                age = timedelta(0)
            if age > PENDING_EXPIRE_AFTER:
                def _expire(row_id=row["id"]):
                    return (
                        db.table("billing_transactions")
                        .update({"status": "expired", "updated_at": _now()})
                        .eq("id", row_id)
                        .execute()
                    )
                await asyncio.to_thread(_expire)
                logger.info("Expired unresolved pending transaction %s (tx_ref=%s)", row["id"], tx_ref)


async def apply_proration(tenant_id: str, old_plan: str) -> float:
    """Calculate the remaining value of the old plan and issue it as account credit."""
    db = get_supabase()
    
    def _get():
        return db.table("tenants").select("next_billing_date").eq("id", tenant_id).limit(1).execute()
        
    try:
        res = await asyncio.to_thread(_get)
        if not res.data:
            return 0.0
            
        next_billing_str = res.data[0].get("next_billing_date")
        if not next_billing_str:
            return 0.0
            
        amount = plan_amount_ngn(old_plan)
        if not amount:
            return 0.0
            
        # Time-based delta calculation
        next_billing = datetime.fromisoformat(next_billing_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        
        if next_billing <= now:
            return 0.0
            
        days_remaining = (next_billing - now).days
        total_days = 30 # 30-day billing cycles
        if days_remaining > total_days:
            days_remaining = total_days
            
        prorated_credit = amount * (days_remaining / total_days)
        if prorated_credit > 0:
            await ledger.credit(tenant_id, prorated_credit, "proration", metadata={"old_plan": old_plan})
            await add_transaction(
                tenant_id=tenant_id,
                amount=prorated_credit,
                tx_type="adjustment",
                status="successful",
                description=f"Proration credit for unused time on {old_plan}"
            )
            logger.info("Prorated %s days of %s for tenant %s: +₦%.2f", days_remaining, old_plan, tenant_id, prorated_credit)
            return prorated_credit
            
    except Exception as e:
        logger.error("Proration calculation failed for %s: %s", tenant_id, e)
        
    return 0.0


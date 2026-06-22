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
from app.services.flutterwave import initialize_payment, tokenized_charge, verify_transaction
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
            
            # 2. Record Transaction
            await add_transaction(
                tenant_id=tenant_id,
                amount=amount,
                tx_type="subscription" if plan else "topup",
                status="successful",
                currency=currency,
                flw_ref=str(flw_id) if flw_id else flw_tx_ref,
                description=f"Flutterwave {plan or 'Balance'} Payment"
            )
            
            # 3. Update Balance (if it was a manual topup not tied to a specific plan)
            if not plan:
                db = get_supabase()
                def _add_bal():
                    return db.rpc("increment_tenant_balance", {"p_tenant": tenant_id, "p_amount": amount}).execute()
                await asyncio.to_thread(_add_bal)

            logger.info("Processed successful charge for tenant %s via Flutterwave", tenant_id)
        else:
            if tenant_id:
                await add_transaction(
                    tenant_id=tenant_id,
                    amount=amount or 0,
                    tx_type="subscription",
                    status="failed",
                    flw_ref=flw_tx_ref,
                    description=f"Failed transaction: {data.get('processor_response', 'Unknown error')}"
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
                # Failed - mark past_due
                await _update_tenant("id", tenant_id, {"subscription_status": "past_due"})
                
                failure_reason = charge.get('processor_response', 'Unknown error')
                await add_transaction(
                    tenant_id=tenant_id,
                    amount=amount,
                    tx_type="subscription",
                    status="failed",
                    flw_ref=tx_ref,
                    description=f"Auto-renewal failed: {failure_reason}"
                )
                
                # Send smart dunning notification
                await notify_billing_failure(tenant_id, amount, failure_reason)
                
        except Exception as e:
            logger.error("Failed to process renewal for tenant %s: %s", tenant_id, e)

async def notify_billing_failure(tenant_id: str, amount: float, reason: str) -> None:
    """Send a dunning notification (WhatsApp/Email) to the tenant's primary agent."""
    db = get_supabase()
    
    def _get_agent():
        return db.table("agents").select("whatsapp").eq("tenant_id", tenant_id).eq("active", True).limit(1).execute()
        
    try:
        res = await asyncio.to_thread(_get_agent)
        if not res.data:
            logger.info("No active agent found for tenant %s; skipping WhatsApp dunning.", tenant_id)
            return
            
        whatsapp = res.data[0].get("whatsapp")
        if not whatsapp:
            return
            
        from app.services.messaging import send_outbound_message
        msg = (
            f"⚠️ *Neoscona Billing Alert*\n\n"
            f"Your automated subscription renewal of ₦{amount:,.0f} failed.\n"
            f"Reason: {reason}\n\n"
            f"Please update your payment method at https://app.neoscona.xyz/billing to avoid service interruption."
        )
        await send_outbound_message(whatsapp, msg, source="dunning")
        logger.info("Sent WhatsApp dunning notification to %s for tenant %s", whatsapp, tenant_id)
    except Exception as e:
        logger.error("Failed to send dunning notice for tenant %s: %s", tenant_id, e)


async def verify_pending_transaction(tx_ref: str) -> bool:
    """Manually pull transaction status from Flutterwave (fallback for missing webhooks)."""
    # ... placeholder
    return False


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
            def _add_bal():
                return db.rpc("increment_tenant_balance", {"p_tenant": tenant_id, "p_amount": prorated_credit}).execute()
            await asyncio.to_thread(_add_bal)
            
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


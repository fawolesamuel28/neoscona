"""Staged dunning for failed subscription renewals.

Replaces the old one-shot WhatsApp ping with a retry schedule:

    day 0  (immediate, from billing.process_automated_renewals on first failure)
           -> stage 1, WhatsApp warning, next action in 3 days
    day 3  -> retry charge; on failure, stage 2, WhatsApp + email, next action in 4 days
    day 7  -> retry charge; on failure, stage 3, final WhatsApp + email warning, next action in 3 days
    day 10 -> retry charge; on failure, subscription_status='canceled' (hard stop —
              reuses the existing check in app.core.entitlements.reply_allowed)

`process_dunning()` is the daily driver (Celery beat); it only advances tenants
whose `dunning_next_action_at` has passed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.billing.plans import plan_amount_ngn
from app.db.supabase import get_supabase
from app.services.email import send_email
from app.services.flutterwave import tokenized_charge
from app.services.messaging import send_outbound_message

logger = logging.getLogger(__name__)

# stage -> days until the *next* escalation if this attempt also fails.
_NEXT_DELAY_DAYS = {1: 4, 2: 3}
_MAX_STAGE = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _tenant_whatsapp(tenant_id: str) -> Optional[str]:
    db = get_supabase()

    def _get():
        return (
            db.table("agents")
            .select("whatsapp")
            .eq("tenant_id", tenant_id)
            .eq("active", True)
            .limit(1)
            .execute()
        )

    res = await asyncio.to_thread(_get)
    return (res.data[0].get("whatsapp") if res.data else None) or None


async def _notify(tenant_id: str, billing_email: Optional[str], subject: str, body_text: str) -> None:
    whatsapp = await _tenant_whatsapp(tenant_id)
    if whatsapp:
        try:
            await send_outbound_message(whatsapp, body_text, source="dunning")
            logger.info("Sent WhatsApp dunning notice to %s for tenant %s", whatsapp, tenant_id)
        except Exception as exc:
            logger.error("Failed to send WhatsApp dunning notice for tenant %s: %s", tenant_id, exc)
    if billing_email:
        html = f"<p>{body_text}</p>".replace("\n", "<br>")
        await send_email(billing_email, subject, html)


async def notify_billing_failure(tenant_id: str, amount: float, reason: str) -> None:
    """Stage-1 (immediate) dunning notice sent right when a renewal charge fails."""
    db = get_supabase()

    def _get_tenant():
        return db.table("tenants").select("billing_email").eq("id", tenant_id).limit(1).execute()

    res = await asyncio.to_thread(_get_tenant)
    billing_email = (res.data[0].get("billing_email") if res.data else None) or None

    msg = (
        f"⚠️ *Neoscona Billing Alert*\n\n"
        f"Your automated subscription renewal of ₦{amount:,.0f} failed.\n"
        f"Reason: {reason}\n\n"
        f"Please update your payment method at https://app.neoscona.xyz/billing to avoid service interruption."
    )
    await _notify(tenant_id, billing_email, "Your Neoscona payment failed", msg)


async def _retry_charge(tenant: dict[str, Any]) -> dict[str, Any]:
    from uuid import uuid4

    tenant_id = tenant["id"]
    plan = tenant.get("plan")
    token = tenant.get("flw_card_token")
    email = tenant.get("flw_token_email") or tenant.get("billing_email")
    amount = plan_amount_ngn(plan)
    if not amount or not token:
        return {"status": "skipped"}
    tx_ref = f"dun-{tenant_id}-{uuid4().hex[:6]}-{int(_now().timestamp())}"
    return await tokenized_charge(token, email, amount, tx_ref)


async def process_dunning() -> None:
    """Advance every past_due tenant whose next dunning action is due."""
    from app.services.billing import add_transaction, update_tenant_billing_fields

    db = get_supabase()
    now = _now()

    def _get_due():
        return (
            db.table("tenants")
            .select(
                "id, plan, billing_email, flw_card_token, flw_token_email, "
                "dunning_stage, dunning_next_action_at"
            )
            .eq("subscription_status", "past_due")
            .gt("dunning_stage", 0)
            .lte("dunning_next_action_at", now.isoformat())
            .execute()
        )

    res = await asyncio.to_thread(_get_due)
    tenants = res.data or []

    for tenant in tenants:
        tenant_id = tenant["id"]
        stage = tenant.get("dunning_stage") or 1
        amount = plan_amount_ngn(tenant.get("plan")) or 0

        try:
            charge = await _retry_charge(tenant)
        except Exception as exc:
            logger.error("Dunning retry charge failed for tenant %s: %s", tenant_id, exc)
            charge = {"status": "failed"}

        if charge.get("status") == "successful":
            next_date = now + timedelta(days=30)
            await update_tenant_billing_fields(tenant_id, {
                "subscription_status": "active",
                "dunning_stage": 0,
                "dunning_next_action_at": None,
                "next_billing_date": next_date.isoformat(),
            })
            await add_transaction(
                tenant_id=tenant_id, amount=amount, tx_type="subscription",
                status="successful", description=f"Dunning retry succeeded (was stage {stage})",
            )
            logger.info("Dunning retry succeeded for tenant %s at stage %s", tenant_id, stage)
            continue

        if stage >= _MAX_STAGE:
            await update_tenant_billing_fields(tenant_id, {
                "subscription_status": "canceled",
                "dunning_stage": 0,
                "dunning_next_action_at": None,
            })
            await _notify(
                tenant_id, tenant.get("billing_email"),
                "Your Neoscona subscription has been canceled",
                "After repeated failed payment attempts, your Neoscona subscription has been "
                "canceled. Resubscribe any time at https://app.neoscona.xyz/billing.",
            )
            logger.info("Dunning exhausted for tenant %s; subscription canceled", tenant_id)
            continue

        next_stage = stage + 1
        delay = _NEXT_DELAY_DAYS.get(stage, 3)
        await update_tenant_billing_fields(tenant_id, {
            "dunning_stage": next_stage,
            "dunning_next_action_at": (now + timedelta(days=delay)).isoformat(),
        })
        urgency = "final warning" if next_stage == _MAX_STAGE else "reminder"
        await _notify(
            tenant_id, tenant.get("billing_email"),
            f"Neoscona billing {urgency}",
            f"We still couldn't charge your card for ₦{amount:,.0f}. "
            f"Please update your payment method at https://app.neoscona.xyz/billing "
            f"— your subscription will be canceled if this isn't resolved soon.",
        )

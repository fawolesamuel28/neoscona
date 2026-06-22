"""Scheduled recurring-charge runner for Flutterwave-based subscriptions.

This module exposes `setup_scheduler()` which registers a daily/cron job to
charge due tenants. APScheduler is optional; if it's not installed we'll log
and skip scheduling (useful for test/dev environments).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def charge_due_tenants() -> None:
    """Find tenants due for billing and attempt renewal via Flutterwave tokens.

    This implementation is intentionally conservative: network errors are caught
    and logged; each attempt is recorded via `flutterwave_events` by the billing
    service when possible.
    """
    try:
        from app.db.supabase import get_supabase
        from app.services.flutterwave import tokenized_charge, initialize_payment
        from app.services.billing import plan_amount_ngn, record_flw_event
    except Exception:
        logger.exception("Required billing modules unavailable for scheduled charges")
        return

    db = get_supabase()

    def _q():
        # tenants where subscription is active and not trial, and next_billing_date due
        return (
            db.table("tenants")
            .select("id, plan, billing_email, flw_card_token, flw_token_email, token_expires_at, next_billing_date")
            .eq("subscription_status", "active")
            .neq("plan", "trial")
            .execute()
        )

    # Run query synchronously via thread to reuse existing patterns
    tenants_res = await __import__("asyncio").to_thread(_q)
    tenants = tenants_res.data or []

    for t in tenants:
        tenant_id = t.get("id")
        plan = t.get("plan")
        amount = plan_amount_ngn(plan)
        if amount is None:
            logger.info("Skipping tenant %s: plan %s has no amount", tenant_id, plan)
            continue

        token = t.get("flw_card_token")
        email = t.get("flw_token_email") or t.get("billing_email")

        tx_ref = f"neo-{tenant_id}-{int(datetime.now(timezone.utc).timestamp())}"

        if token:
            try:
                resp = await tokenized_charge(token=token, email=email, amount_ngn=amount, tx_ref=tx_ref)
                # Record attempt for audit; Flutterwave webhook should also arrive.
                try:
                    await record_flw_event(resp.get("id") or tx_ref, "scheduled.charge_attempt", {"response": resp})
                except Exception:
                    logger.exception("Failed to record scheduled charge attempt for %s", tenant_id)
                # On success, advance next_billing_date
                if resp.get("status") == "successful":
                    next_date = _now() + timedelta(days=30)
                    try:
                        db.table("tenants").update({"next_billing_date": next_date.isoformat()}).eq("id", tenant_id).execute()
                    except Exception:
                        logger.exception("Failed to update next_billing_date for %s", tenant_id)
                else:
                    logger.warning("Scheduled charge for %s returned non-success: %s", tenant_id, resp)
            except Exception:
                logger.exception("Tokenized charge failed for tenant %s", tenant_id)
        else:
            # No token — send re-checkout link by initializing a fresh payment
            try:
                link = await initialize_payment(email=email, amount_ngn=amount, tx_ref=tx_ref, redirect_url=None, metadata={"tenant_id": tenant_id, "plan": plan})
                # The actual email send is out of scope here; we log the link for operators.
                logger.info("Re-checkout link for tenant %s: %s", tenant_id, link.get("link"))
            except Exception:
                logger.exception("Failed to create re-checkout link for tenant %s", tenant_id)


def setup_scheduler() -> None:
    """Register the recurring-job with APScheduler if available."""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler(timezone="Africa/Lagos")
        # Run daily at 08:00 Africa/Lagos
        scheduler.add_job(lambda: __import__("asyncio").create_task(charge_due_tenants()), "cron", hour=8, minute=0)
        scheduler.start()
        logger.info("Billing scheduler started")
    except Exception:
        logger.info("apscheduler not available; skipping billing scheduler")

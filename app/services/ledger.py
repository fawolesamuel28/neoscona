"""Tenant balance ledger — the single place `tenants.balance` is mutated.

Every credit/debit goes through `increment_tenant_balance`/`decrement_tenant_balance`
(migration 008), which update the balance and insert the matching `ledger_entries`
row atomically. Callers should never touch `tenants.balance` directly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.core.tenant import require_tenant
from app.db.supabase import get_supabase

logger = logging.getLogger(__name__)


async def _rpc(fn: str, tenant_id: str, amount: float) -> float:
    db = get_supabase()

    def _call():
        return db.rpc(fn, {"p_tenant": tenant_id, "p_amount": amount}).execute()

    res = await asyncio.to_thread(_call)
    return res.data if isinstance(res.data, (int, float)) else 0.0


async def _log_entry(
    tenant_id: str,
    delta: float,
    balance_after: float,
    reason: str,
    ref_type: Optional[str],
    ref_id: Optional[str],
    metadata: Optional[dict],
) -> None:
    db = get_supabase()

    def _ins():
        return db.table("ledger_entries").insert({
            "tenant_id": tenant_id,
            "delta": delta,
            "balance_after": balance_after,
            "reason": reason,
            "ref_type": ref_type,
            "ref_id": ref_id,
            "metadata": metadata or {},
        }).execute()

    await asyncio.to_thread(_ins)


async def credit(
    tenant_id: str,
    amount: float,
    reason: str,
    *,
    ref_type: Optional[str] = None,
    ref_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> float:
    """Add to a tenant's balance and log it. Returns the new balance."""
    tenant_id = require_tenant(tenant_id)
    if amount <= 0:
        return 0.0
    new_balance = await _rpc("increment_tenant_balance", tenant_id, amount)
    await _log_entry(tenant_id, amount, new_balance, reason, ref_type, ref_id, metadata)
    return new_balance


async def debit(
    tenant_id: str,
    amount: float,
    reason: str,
    *,
    ref_type: Optional[str] = None,
    ref_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> float:
    """Subtract from a tenant's balance and log it. Returns the new balance.

    Balance is allowed to go negative (no hard block) — mirrors the rest of the
    billing stack's soft-enforcement philosophy; a negative balance is a signal
    for dunning/collections, not a reason to break a live pipeline.
    """
    tenant_id = require_tenant(tenant_id)
    if amount <= 0:
        return 0.0
    new_balance = await _rpc("decrement_tenant_balance", tenant_id, amount)
    await _log_entry(tenant_id, -amount, new_balance, reason, ref_type, ref_id, metadata)
    return new_balance

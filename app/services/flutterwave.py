"""Minimal Flutterwave integration (NGN) used by the billing stack.

Provides initialize_payment(), verify_transaction(), tokenized_charge(), and
webhook hash verification. Keys are read at call-time to allow hot rotation.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from typing import Any, Optional

import httpx

FLW_BASE = "https://api.flutterwave.com"


def _secret_key() -> str:
    key = os.getenv("FLUTTERWAVE_SECRET_KEY")
    if not key:
        raise RuntimeError("FLUTTERWAVE_SECRET_KEY is not configured")
    return key


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_secret_key()}", "Content-Type": "application/json"}


async def initialize_payment(
    email: str,
    amount_ngn: int,
    tx_ref: str,
    redirect_url: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create a hosted Flutterwave checkout and return the payment link/payload.

    amount_ngn: whole Naira (1 = ₦1)
    tx_ref: application-generated unique reference
    """
    payload: dict[str, Any] = {
        "amount": str(amount_ngn),
        "currency": "NGN",
        "customer": {"email": email},
        "tx_ref": tx_ref,
    }
    if redirect_url:
        payload["redirect_url"] = redirect_url
    if metadata:
        payload["meta"] = metadata

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{FLW_BASE}/v3/payments", json=payload, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    if not data.get("status"):
        raise RuntimeError(f"Flutterwave init failed: {data}")
    return data.get("data", {})


async def verify_transaction(tx_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{FLW_BASE}/v3/transactions/{tx_id}/verify", headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    return data.get("data", {})


async def tokenized_charge(token: str, email: str, amount_ngn: int, tx_ref: str) -> dict[str, Any]:
    """Charge a saved card token (used for renewals). Returns charge response data."""
    payload = {
        "token": token,
        "currency": "NGN",
        "amount": str(amount_ngn),
        "tx_ref": tx_ref,
        "customer": {"email": email},
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{FLW_BASE}/v3/tokenized-charges", json=payload, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    return data.get("data", {})


def verify_webhook_hash(header_val: Optional[str]) -> bool:
    """Constant-time compare against configured FLUTTERWAVE_WEBHOOK_HASH."""
    if not header_val:
        return False
    secret = os.getenv("FLUTTERWAVE_WEBHOOK_HASH")
    if not secret:
        # Fail closed if hash not configured
        return False
    return secrets.compare_digest(header_val, secret)

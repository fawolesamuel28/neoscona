"""Legacy Paystack client preserved for historical reference.

This file is a read-only legacy copy retained under a `_legacy` name and is
no longer imported by the running application. Do not rely on this for new
development; use `app/services/flutterwave.py` instead.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

PAYSTACK_BASE = "https://api.paystack.co"


def _secret_key() -> str:
    key = os.getenv("PAYSTACK_SECRET_KEY")
    if not key:
        raise RuntimeError("PAYSTACK_SECRET_KEY is not configured")
    return key


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_secret_key()}", "Content-Type": "application/json"}


def verify_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    """Validate the x-paystack-signature header (HMAC-SHA512 of the raw body)."""
    if not signature:
        return False
    expected = hmac.new(_secret_key().encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


async def initialize_transaction(
    email: str,
    amount_kobo: int,
    *,
    plan_code: Optional[str] = None,
    callback_url: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Start a Paystack checkout. Returns {authorization_url, access_code, reference}.

    When `plan_code` is set, Paystack creates a subscription on first charge.
    """
    payload: dict[str, Any] = {"email": email, "amount": amount_kobo, "currency": "NGN"}
    if plan_code:
        payload["plan"] = plan_code
    if callback_url:
        payload["callback_url"] = callback_url
    if metadata:
        payload["metadata"] = metadata

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{PAYSTACK_BASE}/transaction/initialize", json=payload, headers=_headers()
        )
        resp.raise_for_status()
        data = resp.json()
    if not data.get("status"):
        raise RuntimeError(f"Paystack init failed: {data.get('message')}")
    return data["data"]


async def verify_transaction(reference: str) -> dict[str, Any]:
    """Confirm a transaction by reference (used after redirect / for reconciliation)."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{PAYSTACK_BASE}/transaction/verify/{reference}", headers=_headers()
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

"""Minimal transactional email via the Resend HTTP API.

No email-sending capability existed in this codebase before this module — it
backs the dunning escalation's email fallback. No-op (logs and returns) when
`RESEND_API_KEY` is unset, so the rest of the app degrades gracefully rather
than failing closed on a missing optional integration.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

RESEND_BASE = "https://api.resend.com"


async def send_email(to: str, subject: str, html: str, *, from_addr: Optional[str] = None) -> bool:
    """Send a transactional email. Returns True on success, False otherwise (never raises)."""
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logger.info("RESEND_API_KEY not set; skipping email to %s (%s)", to, subject)
        return False
    if not to:
        return False

    sender = from_addr or os.getenv("RESEND_FROM_ADDRESS") or "billing@neoscona.xyz"
    payload = {"from": sender, "to": [to], "subject": subject, "html": html}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{RESEND_BASE}/emails",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Failed to send email to %s: %s", to, exc)
        return False

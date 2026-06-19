"""Tenant helpers.

Multi-tenant isolation is enforced by passing a real `tenant_id` into every
tenant-scoped write/read. There is no silent default: `require_tenant` /
`apply_tenant_defaults` RAISE when a tenant_id is missing, so a forgotten scope
fails loudly instead of leaking data into the shared default tenant.

`get_default_tenant_id()` remains ONLY for explicit system contexts — the local
dev auth bypass and legacy/seeded data — never to scope a real user's data.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Seeded default/system tenant (the original "Atlantic Horizons" workspace).
# Used only for the dev bypass and legacy data — NOT for scoping live user writes.
DEFAULT_TENANT_UUID = "a0000000-0000-4000-8000-000000000001"


def get_default_tenant_id() -> str | None:
    """
    The system/legacy default tenant. Use ONLY for explicit system contexts (dev
    auth bypass, legacy backfill). Do not use this to scope a signed-in user's or
    an inbound message's data — resolve the real tenant instead.
    """
    explicit = (os.getenv("DEFAULT_TENANT_ID") or "").strip()
    if explicit:
        return explicit
    return DEFAULT_TENANT_UUID


def require_tenant(tenant_id: Optional[str]) -> str:
    """Return tenant_id or raise — the single chokepoint that forbids unscoped writes."""
    if not tenant_id:
        raise ValueError(
            "tenant_id is required for tenant-scoped data; refusing to default silently."
        )
    return tenant_id


def apply_tenant_defaults(payload: dict[str, Any], tenant_id: Optional[str] = None) -> dict[str, Any]:
    """
    Ensure an insert/upsert payload carries a tenant_id, taking it from the payload
    or the explicit `tenant_id` argument. RAISES if neither is present — we never
    silently stamp the default tenant onto user data.
    """
    payload["tenant_id"] = require_tenant(payload.get("tenant_id") or tenant_id)
    return payload

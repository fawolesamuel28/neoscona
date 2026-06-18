"""Default tenant helpers for single-tenant dashboard and API writes."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Fixed default tenant (seeded in migrations/008_default_tenant_rls.sql)
DEFAULT_TENANT_UUID = "a0000000-0000-4000-8000-000000000001"


def get_default_tenant_id() -> str | None:
    """
    Tenant UUID for server-side writes (voice import, upsert, conversation logs).
    Uses DEFAULT_TENANT_ID from env, falling back to the seeded Atlantic Horizons id.
    """
    explicit = (os.getenv("DEFAULT_TENANT_ID") or "").strip()
    if explicit:
        return explicit
    return DEFAULT_TENANT_UUID


def apply_tenant_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    """Set tenant_id on insert/upsert payloads when not already provided."""
    if payload.get("tenant_id"):
        return payload
    tenant_id = get_default_tenant_id()
    if tenant_id:
        payload["tenant_id"] = tenant_id
    return payload

"""Per-tenant AI agent configuration.

The AI persona (name, company, tone, languages, qualifying fields, extra guardrails,
custom instructions) is stored per tenant in `agent_configs` and injected into the system
prompt at build time. Missing rows / fields fall back to `DEFAULT_CONFIG` (the original
"Amara / Atlantic Horizons" persona), so existing single-tenant behavior is unchanged.

Reads are cached in-process with a short TTL because `get_agent_config` is called on every
inbound message; the cache is invalidated on save. Every path fails soft to defaults — the
message pipeline must never break because config could not be loaded.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.supabase import get_supabase
from app.llm.prompts import DEFAULT_CONFIG

logger = logging.getLogger(__name__)

# Editable fields (everything else in the prompt is fixed scaffolding).
EDITABLE_FIELDS = (
    "agent_name",
    "company_name",
    "tone",
    "languages",
    "qualifying_fields",
    "guardrails",
    "custom_instructions",
    "greeting",
)

# Allowed qualifying fields the agent can be told to collect.
QUALIFYING_CHOICES = ("budget", "location", "property_type", "timeline")

_CACHE_TTL_SECONDS = 60.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _merged(row: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Overlay a DB row (non-null fields win) onto DEFAULT_CONFIG."""
    cfg = dict(DEFAULT_CONFIG)
    for key in EDITABLE_FIELDS:
        val = (row or {}).get(key)
        if val not in (None, "", []):
            cfg[key] = val
    return cfg


async def get_agent_config(tenant_id: Optional[str]) -> dict[str, Any]:
    """Resolved persona config for a tenant (merged over defaults). Fails soft to defaults."""
    if not tenant_id:
        return dict(DEFAULT_CONFIG)

    hit = _cache.get(tenant_id)
    if hit and (time.monotonic() - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]

    try:
        db = get_supabase()

        def _get():
            return (
                db.table("agent_configs")
                .select("*")
                .eq("tenant_id", tenant_id)
                .limit(1)
                .execute()
            )

        res = await asyncio.to_thread(_get)
        row = res.data[0] if res.data else None
        cfg = _merged(row)
        _cache[tenant_id] = (time.monotonic(), cfg)
        return cfg
    except Exception as exc:  # config must never break the pipeline
        logger.warning("get_agent_config failed for %s (using defaults): %s", tenant_id, exc)
        return dict(DEFAULT_CONFIG)


def _sanitize(patch: dict[str, Any]) -> dict[str, Any]:
    """Keep only editable keys; normalize list fields; bound string lengths."""
    out: dict[str, Any] = {}
    for key in EDITABLE_FIELDS:
        if key not in patch:
            continue
        val = patch[key]
        if key in ("languages", "qualifying_fields"):
            if isinstance(val, str):
                val = [v.strip() for v in val.split(",") if v.strip()]
            elif isinstance(val, list):
                val = [str(v).strip() for v in val if str(v).strip()]
            else:
                continue
            if key == "qualifying_fields":
                val = [v for v in val if v in QUALIFYING_CHOICES] or list(DEFAULT_CONFIG[key])
        elif isinstance(val, str):
            limit = 2000 if key in ("custom_instructions", "tone", "guardrails") else 200
            val = val.strip()[:limit]
        out[key] = val
    return out


async def upsert_agent_config(tenant_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Validate + persist a partial config update; returns the merged resolved config."""
    clean = _sanitize(patch)
    db = get_supabase()
    row = {"tenant_id": tenant_id, **clean, "updated_at": datetime.now(timezone.utc).isoformat()}

    def _upsert():
        return db.table("agent_configs").upsert(row, on_conflict="tenant_id").execute()

    res = await asyncio.to_thread(_upsert)
    saved = res.data[0] if res.data else row
    cfg = _merged(saved)
    _cache[tenant_id] = (time.monotonic(), cfg)  # refresh cache immediately
    return cfg

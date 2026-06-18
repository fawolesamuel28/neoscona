"""Public client configuration for the browser dashboard.

Exposes only browser-safe values: the Supabase project URL and the **anon**
public key (designed to ship to clients), plus the auth mode. No secrets here —
the service key and JWT secret never leave the server.
"""

from __future__ import annotations

import os

from fastapi import APIRouter

router = APIRouter(tags=["Config"])


@router.get("/config")
async def public_config() -> dict:
    """Front-end bootstrap config. Safe to call without authentication."""
    env = (os.getenv("ENVIRONMENT") or "development").lower()
    auth_disabled = (os.getenv("AUTH_DISABLED") or "").lower() in ("1", "true", "yes")
    return {
        "supabase_url": os.getenv("SUPABASE_URL") or "",
        # Anon/public key — intended for browser use (RLS-protected).
        "supabase_anon_key": os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY") or "",
        # When true (dev only, never in production) the dashboard skips login.
        "auth_disabled": auth_disabled and env != "production",
        "environment": env,
    }

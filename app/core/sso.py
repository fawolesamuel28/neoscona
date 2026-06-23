"""Cross-subdomain single sign-on bridge for the Neoscona suite.

Supabase Auth (GoTrue) is the identity provider. The browser obtains a session
via the Supabase JS SDK, which keeps it in localStorage — that storage is
per-origin, so it cannot be shared across `console.neoscona.xyz`,
`app.neoscona.xyz`, etc. To get one sign-on across the console and every product
surface, the verified session is also written to HttpOnly cookies scoped to the
parent domain (`.neoscona.xyz`).

The backend auth dependency (`app.core.auth`) already accepts the access token
from either the `Authorization` header OR the `nsc_access` cookie, so once these
cookies are set every product path is authenticated with no extra wiring.

This module provides:
  * cookie set/clear helpers (parent-domain, HttpOnly, Secure),
  * `/auth/session`, `/auth/refresh`, `/auth/logout` endpoints, and
  * `page_session_ok()` to gate server-rendered console pages (redirect to
    /login on miss, instead of the JSON 401 that API routes return).

JWT verification is delegated to `app.core.auth._decode_token` — there is one
verifier for the whole suite.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core import auth as _auth
from app.core.auth import SSO_COOKIE_NAME, _decode_token
from app.services.onboarding import membership_tenant_id, provision_workspace
import asyncio
from app.core.logger import get_logger

logger = get_logger(__name__)

# ── Config (env) ──────────────────────────────────────────────────────────────
SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
_GOTRUE = f"{SUPABASE_URL}/auth/v1" if SUPABASE_URL else ""

ACCESS_COOKIE = SSO_COOKIE_NAME  # "nsc_access"
REFRESH_COOKIE = os.environ.get("SSO_REFRESH_COOKIE", "nsc_refresh")

# Empty COOKIE_DOMAIN → host-only cookie (correct for localhost). In production
# set COOKIE_DOMAIN=.neoscona.xyz so the cookie is shared across subdomains.
COOKIE_DOMAIN = os.environ.get("COOKIE_DOMAIN", "").strip() or None
COOKIE_SECURE = (os.environ.get("COOKIE_SECURE", "false").lower() in ("1", "true", "yes"))

# Access cookie is short-lived (matches Supabase access-token TTL ~1h); the
# refresh cookie is long-lived and scoped to /auth so it is only ever sent to
# the refresh/logout endpoints.
ACCESS_MAX_AGE = int(os.environ.get("SSO_ACCESS_MAX_AGE", str(60 * 60)))
REFRESH_MAX_AGE = int(os.environ.get("SSO_REFRESH_MAX_AGE", str(60 * 60 * 24 * 30)))
REFRESH_PATH = "/auth"


# ── Cookie helpers ──────────────────────────────────────────────────────────────
def set_session_cookies(resp: Response, access_token: str, refresh_token: Optional[str]) -> None:
    """Write the SSO cookies on the parent domain. HttpOnly so they are never
    script-readable; the SPA keeps its own copy in localStorage for fetches."""
    resp.set_cookie(
        ACCESS_COOKIE, access_token,
        max_age=ACCESS_MAX_AGE, httponly=True, secure=COOKIE_SECURE,
        samesite="lax", domain=COOKIE_DOMAIN, path="/",
    )
    if refresh_token:
        resp.set_cookie(
            REFRESH_COOKIE, refresh_token,
            max_age=REFRESH_MAX_AGE, httponly=True, secure=COOKIE_SECURE,
            samesite="lax", domain=COOKIE_DOMAIN, path=REFRESH_PATH,
        )


def clear_session_cookies(resp: Response) -> None:
    """Expire both cookies. Domain/Path must match how they were set or the
    browser keeps them."""
    resp.delete_cookie(ACCESS_COOKIE, domain=COOKIE_DOMAIN, path="/")
    resp.delete_cookie(REFRESH_COOKIE, domain=COOKIE_DOMAIN, path=REFRESH_PATH)


# ── GoTrue REST (refresh / sign-out) ────────────────────────────────────────────
def _gotrue_headers() -> dict:
    return {"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"}


def gotrue_refresh(refresh_token: str) -> dict:
    """Exchange a refresh token for a fresh session via GoTrue."""
    if not _GOTRUE or not SUPABASE_ANON_KEY:
        raise HTTPException(status_code=500, detail="Authentication is not configured.")
    try:
        resp = httpx.post(
            f"{_GOTRUE}/token", params={"grant_type": "refresh_token"},
            headers=_gotrue_headers(), json={"refresh_token": refresh_token}, timeout=15,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Could not reach the authentication service.") from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    return resp.json()


def gotrue_sign_out(access_token: str) -> None:
    """Best-effort server-side logout; failures are ignored (cookies are cleared
    regardless)."""
    if not _GOTRUE:
        return
    try:
        httpx.post(
            f"{_GOTRUE}/logout",
            headers={**_gotrue_headers(), "Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
    except httpx.HTTPError:
        pass


# ── Server-rendered page guard ───────────────────────────────────────────────────
def page_session_ok(request: Request) -> bool:
    """True if the request carries a valid Supabase session cookie (or auth is
    disabled for dev). Gates server-rendered console pages; the SPA still
    enforces membership/tenant via the API, so a freshly-signed-up user without
    a tenant is NOT bounced off the page here."""
    if _auth._auth_disabled():
        return True
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        return False
    try:
        _decode_token(token)
        return True
    except HTTPException:
        return False


# ── Endpoints ───────────────────────────────────────────────────────────────────
router = APIRouter(tags=["Auth/SSO"])


class SessionBody(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None


@router.post("/auth/session")
async def create_session(body: SessionBody):
    """Bridge a Supabase JS session into parent-domain SSO cookies. The access
    token is verified before we trust it."""
    claims = _decode_token(body.access_token)  # raises 401 if invalid/expired
    resp = JSONResponse({"ok": True})
    set_session_cookies(resp, body.access_token, body.refresh_token)

    # Provision a workspace in the background for signups/logins that bypass
    # the explicit /api/signup flow (e.g. OAuth or JS flows that only bridge
    # the session). This is best-effort and must not block the response.
    async def _bg_provision():
        try:
            user_id = claims.get("sub")
            email = claims.get("email")
            if not user_id:
                return
            # If the user already has a membership, nothing to do.
            existing = await membership_tenant_id(user_id)
            if existing:
                return
            # Provision with an empty company_name -> defaults to "My Workspace".
            await provision_workspace(user_id, email, "")
        except Exception:
            logger.exception("Background workspace provisioning failed for user %s", claims.get("sub"))

    try:
        asyncio.create_task(_bg_provision())
    except Exception:
        logger.exception("Failed to spawn background provisioning task")

    return resp


@router.post("/auth/refresh")
async def refresh_session(request: Request):
    """Rotate the access cookie using the refresh cookie."""
    rt = request.cookies.get(REFRESH_COOKIE)
    if not rt:
        raise HTTPException(status_code=401, detail="No refresh token")
    data = gotrue_refresh(rt)
    resp = JSONResponse({"ok": True})
    set_session_cookies(resp, data.get("access_token", ""), data.get("refresh_token"))
    return resp


@router.post("/auth/logout")
async def logout(request: Request):
    """GoTrue sign-out + clear both SSO cookies."""
    access = request.cookies.get(ACCESS_COOKIE)
    if access:
        gotrue_sign_out(access)
    resp = JSONResponse({"ok": True})
    clear_session_cookies(resp)
    return resp

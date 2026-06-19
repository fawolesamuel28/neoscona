"""
Phase 1 authentication & authorization.

Identity provider is **Supabase Auth**. The frontend obtains a Supabase access
token (a JWT) and sends it as `Authorization: Bearer <token>`. This module:

  1. verifies that JWT (HS256, signed with the project's SUPABASE_JWT_SECRET),
  2. resolves the user's tenant + role from the `memberships` table, and
  3. exposes FastAPI dependencies that yield a `Principal` to route handlers.

Tenant isolation is enforced in two layers: (1) handlers pass `principal.tenant_id`
into every query (the deterministic primary guard), and (2) on request paths the
verified user token is stashed via `set_request_token` so service code can build a
user-scoped Supabase client (`get_request_client`) whose queries run under RLS
(`auth.uid()` joined to `memberships`) as a defense-in-depth backstop.

Local-dev escape hatch: set AUTH_DISABLED=true to inject an owner principal for
the default tenant. It is **ignored when ENVIRONMENT=production**.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, WebSocket, status

from app.core.logger import get_logger
from app.core.tenant import get_default_tenant_id
from app.db.supabase import get_supabase, set_request_token

logger = get_logger(__name__)

# Role ranking for hierarchical checks (owner > admin > agent > viewer).
ROLE_HIERARCHY: dict[str, int] = {"viewer": 0, "agent": 1, "admin": 2, "owner": 3}

# Name of the cross-subdomain SSO cookie that may carry the access token in
# addition to the Authorization header. The umbrella app sets this on
# `.neoscona.xyz` so one sign-on covers the console and every product surface;
# the header always takes precedence when both are present.
SSO_COOKIE_NAME = os.getenv("SSO_COOKIE_NAME", "nsc_access")


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, scoped to a single tenant for this request."""

    user_id: str
    email: Optional[str]
    tenant_id: str
    role: str

    def has_role(self, minimum: str) -> bool:
        return ROLE_HIERARCHY.get(self.role, -1) >= ROLE_HIERARCHY.get(minimum, 99)


@dataclass(frozen=True)
class AuthUser:
    """An authenticated Supabase user with no org context yet (used at signup)."""

    user_id: str
    email: Optional[str]


# ── Dev bypass ────────────────────────────────────────────────────────────────
def _auth_disabled() -> bool:
    env = (os.getenv("ENVIRONMENT") or "development").lower()
    disabled = (os.getenv("AUTH_DISABLED") or "").lower() in ("1", "true", "yes")
    if disabled and env == "production":
        logger.error("AUTH_DISABLED is set but ignored because ENVIRONMENT=production.")
        return False
    return disabled


def _dev_principal() -> Principal:
    tenant_id = get_default_tenant_id() or "00000000-0000-0000-0000-000000000000"
    logger.warning(
        "AUTH BYPASS active (dev only) — injecting owner principal for tenant %s",
        tenant_id,
    )
    return Principal(user_id="dev-user", email="dev@local", tenant_id=tenant_id, role="owner")


# ── JWT verification ──────────────────────────────────────────────────────────
# Supabase supports two signing schemes:
#   • asymmetric JWT signing keys (ES256/RS256) — current default; access tokens
#     are verified against the project's PUBLIC keys at the JWKS endpoint.
#   • legacy shared secret (HS256) via SUPABASE_JWT_SECRET — kept as a fallback
#     so tokens issued during the migration window still verify.
_DECODE_OPTS = {"require": ["exp", "sub"]}
_jwks_client_instance = None


def get_jwks_client():
    """Cached PyJWKClient pointed at the project's JWKS (public signing keys)."""
    global _jwks_client_instance
    if _jwks_client_instance is None:
        url = os.getenv("SUPABASE_JWKS_URL") or (
            (os.getenv("SUPABASE_URL") or "").rstrip("/") + "/auth/v1/.well-known/jwks.json"
        )
        if not url.startswith("http"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server auth is not configured (SUPABASE_URL / SUPABASE_JWKS_URL missing).",
            )
        _jwks_client_instance = jwt.PyJWKClient(url)
    return _jwks_client_instance


def _decode_token(token: str) -> dict:
    try:
        alg = jwt.get_unverified_header(token).get("alg", "")
    except jwt.InvalidTokenError as exc:
        logger.info("Rejected JWT (bad header): %s", exc)
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    try:
        if alg == "HS256":
            secret = os.getenv("SUPABASE_JWT_SECRET")
            if not secret or len(secret) < 32:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Server auth is not configured (SUPABASE_JWT_SECRET missing or too short).",
                )
            return jwt.decode(
                token, secret, algorithms=["HS256"],
                audience="authenticated", options=_DECODE_OPTS,
            )

        # Asymmetric: fetch the public key whose `kid` matches the token header.
        try:
            signing_key = get_jwks_client().get_signing_key_from_jwt(token)
            key_val = signing_key.key
        except Exception:
            # Fallback: if Supabase omit 'kid', just use the first available key
            keys = get_jwks_client().get_signing_keys()
            if not keys:
                raise ValueError("No keys found in Supabase JWKS")
            key_val = keys[0].key

        return jwt.decode(
            token, key_val, algorithms=["ES256", "RS256"],
            audience="authenticated", options=_DECODE_OPTS,
        )
    except HTTPException:
        raise  # config errors (e.g. missing secret) keep their 500
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception as exc:
        # Any verification failure — bad signature, JWKS/key errors, or a missing
        # `cryptography` install for ES256/RS256 — is a 401, never a 500. The
        # exception type is logged so misconfig (e.g. cryptography absent) is visible.
        logger.warning("Rejected JWT (%s): %s", type(exc).__name__, exc)
        raise HTTPException(status_code=401, detail="Invalid or unverifiable authentication token")


def _token_from_header_or_cookie(
    authorization: Optional[str], cookie_token: Optional[str]
) -> str:
    """Extract the access token from the Authorization header, falling back to the
    SSO cookie. The header wins when both are present, so existing API clients are
    unaffected and cross-subdomain cookie SSO is purely additive."""
    # `isinstance` guards against FastAPI's Header/Cookie sentinel default when
    # these dependencies are invoked directly (e.g. in unit tests) rather than
    # resolved by the framework.
    if isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    if isinstance(cookie_token, str) and cookie_token:
        return cookie_token.strip()
    raise HTTPException(
        status_code=401,
        detail="Missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _lookup_membership(user_id: str, requested_tenant: Optional[str]) -> tuple[str, str]:
    """
    Resolve (tenant_id, role) for a user.

    If `requested_tenant` is given (via X-Org-Id), the user must be a member of it.
    Otherwise we pick their highest-privilege membership. Runs with the
    service-role client (trusted server-side lookup).
    """
    db = get_supabase()
    res = (
        db.table("memberships")
        .select("tenant_id, role")
        .eq("user_id", user_id)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=403, detail="User has no organization membership")

    if requested_tenant:
        for r in rows:
            if str(r.get("tenant_id")) == str(requested_tenant):
                return r["tenant_id"], r["role"]
        raise HTTPException(status_code=403, detail="Not a member of the requested organization")

    best = max(rows, key=lambda r: ROLE_HIERARCHY.get(r.get("role"), -1))
    return best["tenant_id"], best["role"]


# ── HTTP dependencies ─────────────────────────────────────────────────────────
async def get_current_principal(
    authorization: Optional[str] = Header(default=None),
    x_org_id: Optional[str] = Header(default=None),
    sso_cookie: Optional[str] = Cookie(default=None, alias=SSO_COOKIE_NAME),
) -> Principal:
    """FastAPI dependency: require a valid Supabase token + tenant membership.

    The token is read from the Authorization header, or the SSO cookie as a
    fallback (header wins), enabling cross-subdomain single sign-on.
    """
    if _auth_disabled():
        return _dev_principal()

    token = _token_from_header_or_cookie(authorization, sso_cookie)
    claims = _decode_token(token)
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing subject")

    # Stash the verified token so tenant-scoped service code can build a
    # user-scoped (RLS-enforced) Supabase client for this request.
    set_request_token(token)

    tenant_id, role = await asyncio.to_thread(_lookup_membership, user_id, x_org_id)
    return Principal(user_id=user_id, email=claims.get("email"), tenant_id=tenant_id, role=role)


async def get_authenticated_user(
    authorization: Optional[str] = Header(default=None),
    sso_cookie: Optional[str] = Cookie(default=None, alias=SSO_COOKIE_NAME),
) -> AuthUser:
    """Verify the token but do NOT require an org membership.

    Used by the signup/provisioning endpoint, where a freshly-registered user has
    a valid Supabase session but no tenant yet. Accepts the header or SSO cookie.
    """
    if _auth_disabled():
        return AuthUser(user_id="dev-user", email="dev@local")
    claims = _decode_token(_token_from_header_or_cookie(authorization, sso_cookie))
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing subject")
    return AuthUser(user_id=user_id, email=claims.get("email"))


def require_role(minimum: str):
    """Dependency factory enforcing a minimum role (owner/admin/agent/viewer)."""

    async def _dep(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.has_role(minimum):
            raise HTTPException(
                status_code=403,
                detail=f"Requires '{minimum}' role or higher",
            )
        return principal

    return _dep


# ── WebSocket auth ────────────────────────────────────────────────────────────
async def get_ws_principal(websocket: WebSocket) -> Optional[Principal]:
    """
    Authenticate a WebSocket from `?token=<supabase_jwt>` (optionally `&org_id=`).
    Returns None when authentication fails — caller should close the socket.
    """
    if _auth_disabled():
        return _dev_principal()

    # Prefer the query-param token; fall back to the SSO cookie so the dashboard
    # can connect after cookie-based sign-on without putting the JWT in the URL.
    token = websocket.query_params.get("token") or websocket.cookies.get(SSO_COOKIE_NAME)
    if not token:
        return None
    try:
        claims = _decode_token(token)
        user_id = claims.get("sub")
        if not user_id:
            return None
        set_request_token(token)
        tenant_id, role = await asyncio.to_thread(
            _lookup_membership, user_id, websocket.query_params.get("org_id")
        )
    except HTTPException:
        return None

    return Principal(user_id=user_id, email=claims.get("email"), tenant_id=tenant_id, role=role)

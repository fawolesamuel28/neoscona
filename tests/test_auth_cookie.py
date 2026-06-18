"""Cross-subdomain SSO: the access token may arrive via the Authorization header
OR the SSO cookie (`nsc_access`). The header wins when both are present, so
header-only API clients are unaffected.

No live DB or network — JWTs are minted locally and the membership lookup is
faked, mirroring test_auth.py.
"""

from __future__ import annotations

import asyncio
import time

import jwt
import pytest
from fastapi import HTTPException

from app.core import auth

TEST_SECRET = "x" * 40


def _token(secret=TEST_SECRET, sub="user-1", email="a@b.com", exp_delta=3600,
           aud="authenticated", **extra):
    payload = {"sub": sub, "email": email, "aud": aud, "exp": int(time.time()) + exp_delta}
    payload.update(extra)
    return jwt.encode(payload, secret, algorithm="HS256")


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self._filters: dict = {}

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def execute(self):
        data = [r for r in self._rows if all(r.get(c) == v for c, v in self._filters.items())]
        return _FakeResult(data)


class _FakeSupabase:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        return _FakeQuery(list(self._rows))


@pytest.fixture(autouse=True)
def _base_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("AUTH_DISABLED", raising=False)


def _set_memberships(monkeypatch, rows):
    monkeypatch.setattr(auth, "get_supabase", lambda: _FakeSupabase(rows))


_MEMBER = [{"user_id": "user-1", "tenant_id": "tenant-A", "role": "admin"}]


# ── _token_from_header_or_cookie ──────────────────────────────────────────────
def test_header_only():
    assert auth._token_from_header_or_cookie("Bearer abc", None) == "abc"


def test_cookie_only():
    assert auth._token_from_header_or_cookie(None, "xyz") == "xyz"


def test_header_beats_cookie():
    assert auth._token_from_header_or_cookie("Bearer abc", "xyz") == "abc"


def test_neither_raises_401():
    with pytest.raises(HTTPException) as e:
        auth._token_from_header_or_cookie(None, None)
    assert e.value.status_code == 401


def test_malformed_header_falls_back_to_cookie():
    # A non-bearer Authorization value should not shadow a valid cookie.
    assert auth._token_from_header_or_cookie("Basic zzz", "xyz") == "xyz"


# ── get_current_principal via cookie ──────────────────────────────────────────
def test_principal_from_cookie(monkeypatch):
    _set_memberships(monkeypatch, _MEMBER)
    p = asyncio.run(
        auth.get_current_principal(authorization=None, x_org_id=None, sso_cookie=_token())
    )
    assert (p.tenant_id, p.role, p.user_id) == ("tenant-A", "admin", "user-1")


def test_principal_header_beats_cookie(monkeypatch):
    # Cookie carries a token for a DIFFERENT (invalid) secret; header must win.
    _set_memberships(monkeypatch, _MEMBER)
    p = asyncio.run(
        auth.get_current_principal(
            authorization=f"Bearer {_token()}",
            x_org_id=None,
            sso_cookie=_token(secret="y" * 40),
        )
    )
    assert p.user_id == "user-1"


def test_principal_no_token_at_all(monkeypatch):
    _set_memberships(monkeypatch, _MEMBER)
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth.get_current_principal(authorization=None, x_org_id=None, sso_cookie=None))
    assert e.value.status_code == 401


def test_authenticated_user_from_cookie():
    u = asyncio.run(auth.get_authenticated_user(authorization=None, sso_cookie=_token()))
    assert u.user_id == "user-1" and u.email == "a@b.com"

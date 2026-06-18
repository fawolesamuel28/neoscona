"""Auth: JWT verification + principal/role dependencies.

No live DB or network — JWTs are minted locally and the Supabase membership
lookup is faked. Async dependencies are driven with ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import time

import jwt
import pytest
from fastapi import HTTPException

from app.core import auth

TEST_SECRET = "x" * 40  # >= 32 chars, as _decode_token requires


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


# ── token decoding ────────────────────────────────────────────────────────────
def test_decode_valid():
    assert auth._decode_token(_token())["sub"] == "user-1"


def test_decode_expired():
    with pytest.raises(HTTPException) as e:
        auth._decode_token(_token(exp_delta=-10))
    assert e.value.status_code == 401


def test_decode_tampered_signature():
    with pytest.raises(HTTPException) as e:
        auth._decode_token(_token(secret="y" * 40))
    assert e.value.status_code == 401


def test_decode_missing_secret(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    with pytest.raises(HTTPException) as e:
        auth._decode_token(_token())
    assert e.value.status_code == 500


# ── get_current_principal ───────────────────────────────────────────────────────
def test_missing_bearer():
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth.get_current_principal(authorization=None))
    assert e.value.status_code == 401


def test_principal_with_membership(monkeypatch):
    _set_memberships(monkeypatch, [{"user_id": "user-1", "tenant_id": "tenant-A", "role": "admin"}])
    p = asyncio.run(auth.get_current_principal(authorization=f"Bearer {_token()}", x_org_id=None))
    assert (p.tenant_id, p.role, p.user_id) == ("tenant-A", "admin", "user-1")


def test_principal_no_membership(monkeypatch):
    _set_memberships(monkeypatch, [])
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth.get_current_principal(authorization=f"Bearer {_token()}"))
    assert e.value.status_code == 403


def test_principal_org_selection(monkeypatch):
    _set_memberships(monkeypatch, [
        {"user_id": "user-1", "tenant_id": "tenant-A", "role": "owner"},
        {"user_id": "user-1", "tenant_id": "tenant-B", "role": "agent"},
    ])
    p = asyncio.run(auth.get_current_principal(authorization=f"Bearer {_token()}", x_org_id="tenant-B"))
    assert (p.tenant_id, p.role) == ("tenant-B", "agent")


def test_principal_org_not_member(monkeypatch):
    _set_memberships(monkeypatch, [{"user_id": "user-1", "tenant_id": "tenant-A", "role": "owner"}])
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth.get_current_principal(authorization=f"Bearer {_token()}", x_org_id="tenant-Z"))
    assert e.value.status_code == 403


def test_principal_default_picks_highest_role(monkeypatch):
    _set_memberships(monkeypatch, [
        {"user_id": "user-1", "tenant_id": "tenant-A", "role": "viewer"},
        {"user_id": "user-1", "tenant_id": "tenant-B", "role": "owner"},
    ])
    p = asyncio.run(auth.get_current_principal(authorization=f"Bearer {_token()}", x_org_id=None))
    assert (p.tenant_id, p.role) == ("tenant-B", "owner")


# ── dev bypass ──────────────────────────────────────────────────────────────────
def test_bypass_dev(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DEFAULT_TENANT_ID", "tenant-default")
    p = asyncio.run(auth.get_current_principal(authorization=None))
    assert p.role == "owner" and p.tenant_id == "tenant-default"


def test_bypass_ignored_in_production(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth.get_current_principal(authorization=None))
    assert e.value.status_code == 401


# ── require_role ─────────────────────────────────────────────────────────────────
def test_require_role_denied(monkeypatch):
    _set_memberships(monkeypatch, [{"user_id": "user-1", "tenant_id": "tenant-A", "role": "viewer"}])
    p = asyncio.run(auth.get_current_principal(authorization=f"Bearer {_token()}", x_org_id=None))
    with pytest.raises(HTTPException) as e:
        asyncio.run(auth.require_role("admin")(principal=p))
    assert e.value.status_code == 403


def test_require_role_allowed(monkeypatch):
    _set_memberships(monkeypatch, [{"user_id": "user-1", "tenant_id": "tenant-A", "role": "owner"}])
    p = asyncio.run(auth.get_current_principal(authorization=f"Bearer {_token()}", x_org_id=None))
    assert asyncio.run(auth.require_role("admin")(principal=p)) is p

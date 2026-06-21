"""Voice receptionist: webhook signature/extraction, provider clients, provisioning.

No live network or DB — httpx is faked and DB/registry seams are monkeypatched.
Async paths are driven with ``asyncio.run`` (matching tests/test_auth.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import pytest

from app.webhooks import voice_elevenlabs as wh
from app.services.voice import elevenlabs, krosai, provisioning

WEBHOOK_SECRET = "unit-test-webhook-secret-0123456789"


# ─── Signature verification ─────────────────────────────────────────────────────

def _sign(body: bytes, secret: str = WEBHOOK_SECRET, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v0={mac}"


def test_verify_signature_valid(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = b'{"hello":"world"}'
    assert wh._verify_signature(body, _sign(body)) is True


def test_verify_signature_rejects_tampered_body(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", WEBHOOK_SECRET)
    sig = _sign(b'{"hello":"world"}')
    assert wh._verify_signature(b'{"hello":"evil"}', sig) is False


def test_verify_signature_rejects_wrong_secret(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = b'{"a":1}'
    assert wh._verify_signature(body, _sign(body, secret="other")) is False


def test_verify_signature_rejects_stale_timestamp(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = b'{"a":1}'
    old = int(time.time()) - (wh._MAX_SIGNATURE_AGE_SECONDS + 60)
    assert wh._verify_signature(body, _sign(body, ts=old)) is False


def test_verify_signature_fails_closed_without_secret(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_WEBHOOK_SECRET", raising=False)
    body = b'{"a":1}'
    assert wh._verify_signature(body, _sign(body)) is False


def test_verify_signature_rejects_missing_header(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_WEBHOOK_SECRET", WEBHOOK_SECRET)
    assert wh._verify_signature(b"{}", None) is False


# ─── data_collection_results extraction ─────────────────────────────────────────

def test_collected_value_dict():
    assert wh._collected({"budget": {"value": "50m", "rationale": "x"}}, "budget") == "50m"


def test_collected_value_bare_scalar():
    assert wh._collected({"budget": "90m"}, "budget") == "90m"


def test_collected_missing_and_empty():
    assert wh._collected({}, "budget") is None
    assert wh._collected({"budget": {"value": ""}}, "budget") is None
    assert wh._collected({"budget": {"value": None}}, "budget") is None


# ─── Provider clients: request shaping (httpx faked) ─────────────────────────────

class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.content = b"x"
        self.text = text

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Captures the single request made and returns a canned response."""

    last_call: dict = {}
    response = _FakeResponse(payload={})

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, headers=None, json=None, params=None):
        _FakeAsyncClient.last_call = {
            "method": method, "url": url, "headers": headers or {},
            "json": json, "params": params,
        }
        return _FakeAsyncClient.response


def test_elevenlabs_create_agent_shapes_request(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    _FakeAsyncClient.response = _FakeResponse(payload={"agent_id": "agent_123"})
    monkeypatch.setattr(elevenlabs.httpx, "AsyncClient", _FakeAsyncClient)

    agent_id = asyncio.run(elevenlabs.create_agent(name="Tola", prompt="be nice"))
    assert agent_id == "agent_123"
    call = _FakeAsyncClient.last_call
    assert call["method"] == "POST"
    assert call["url"].endswith("/v1/convai/agents/create")
    assert call["headers"]["xi-api-key"] == "sk_test"
    assert call["json"]["conversation_config"]["agent"]["prompt"]["prompt"] == "be nice"


def test_elevenlabs_create_agent_raises_without_agent_id(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    _FakeAsyncClient.response = _FakeResponse(payload={})
    monkeypatch.setattr(elevenlabs.httpx, "AsyncClient", _FakeAsyncClient)
    with pytest.raises(elevenlabs.ElevenLabsError):
        asyncio.run(elevenlabs.create_agent(name="x", prompt="y"))


def test_krosai_endpoint_uses_native_elevenlabs_type(monkeypatch):
    monkeypatch.setenv("KROSAI_API_KEY", "kros_test_1")
    _FakeAsyncClient.response = _FakeResponse(payload={"id": "ep_1", "type": "elevenlabs"})
    monkeypatch.setattr(krosai.httpx, "AsyncClient", _FakeAsyncClient)

    ep = asyncio.run(krosai.create_elevenlabs_endpoint(name="reva", elevenlabs_agent_id="agent_123"))
    assert ep["id"] == "ep_1"
    call = _FakeAsyncClient.last_call
    assert call["url"].endswith("/endpoints")
    assert call["json"]["type"] == "elevenlabs"
    assert call["json"]["provider_config"]["elevenlabs_agent_id"] == "agent_123"
    assert call["headers"]["Authorization"] == "Bearer kros_test_1"


def test_krosai_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("KROSAI_API_KEY", "kros_test_1")
    _FakeAsyncClient.response = _FakeResponse(status_code=402, text="INSUFFICIENT_BALANCE")
    monkeypatch.setattr(krosai.httpx, "AsyncClient", _FakeAsyncClient)
    with pytest.raises(krosai.KrosAIError):
        asyncio.run(krosai.purchase_number(inventory_id="inv_1"))


# ─── Provisioning rollback ───────────────────────────────────────────────────────

def test_provision_rolls_back_when_number_purchase_fails(monkeypatch):
    """If the number purchase fails, the agent + endpoint must be torn down."""
    released, deleted_ep, deleted_agent = [], [], []

    async def _no_existing(_tenant):
        return None

    async def _create_agent(**k):
        return "agent_X"

    async def _create_ep(**k):
        return {"id": "ep_X"}

    async def _purchase(**k):
        raise krosai.KrosAIError("boom: no balance")

    async def _release(pid):
        released.append(pid)

    async def _del_ep(eid):
        deleted_ep.append(eid)

    async def _del_agent(aid):
        deleted_agent.append(aid)

    monkeypatch.setattr(provisioning, "get_voice_agent", _no_existing)
    monkeypatch.setattr(provisioning.elevenlabs, "create_agent", _create_agent)
    monkeypatch.setattr(provisioning.elevenlabs, "delete_agent", _del_agent)
    monkeypatch.setattr(provisioning.krosai, "create_elevenlabs_endpoint", _create_ep)
    monkeypatch.setattr(provisioning.krosai, "purchase_number", _purchase)
    monkeypatch.setattr(provisioning.krosai, "release_number", _release)
    monkeypatch.setattr(provisioning.krosai, "delete_endpoint", _del_ep)

    persona = provisioning.Persona(name="x", prompt="y", first_message="", language="en", voice_id=None)
    with pytest.raises(krosai.KrosAIError):
        asyncio.run(provisioning.provision_receptionist(
            "11111111-1111-4111-8111-aaaaaaaaaaaa", persona=persona, inventory_id="inv_1",
        ))

    # Number never bought, so nothing to release; endpoint + agent rolled back.
    assert released == []
    assert deleted_ep == ["ep_X"]
    assert deleted_agent == ["agent_X"]


def test_build_persona_from_form_defaults_prompt():
    persona = provisioning.build_persona_from_form({"name": "Tola", "company_name": "Acme"})
    assert persona["name"] == "Tola"
    assert "Tola" in persona["prompt"] and "Acme" in persona["prompt"]

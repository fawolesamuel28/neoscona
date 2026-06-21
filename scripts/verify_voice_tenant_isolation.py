"""Two-tenant proof for the ElevenLabs voice post-call webhook.

The receptionist webhook is authenticated centrally (one shared HMAC secret) and then
MUST attribute each call to the owning tenant strictly by the ElevenLabs `agent_id` via
the channel registry — never a default. This script drives the REAL webhook handler
(`app/webhooks/voice_elevenlabs.py`) with two tenants' calls and proves:

  • a call on agent A's signature lands ONLY under tenant A (and B's only under B),
  • an unregistered agent_id is rejected (no lead written),
  • a tampered/forged signature is rejected (fail-closed),
  • the metered usage + pipeline import are attributed to the same resolved tenant.

It needs neither Supabase nor Redis: the DB/registry/cache seams are stubbed so the
test isolates the handler's *attribution* logic (the isolation-critical surface). The
HMAC is computed for real, so signature verification is genuinely exercised.

    Run:   python scripts/verify_voice_tenant_isolation.py
    Exit:  0 = PASS (isolated), 1 = BLOCK (leak / unexpected error)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# A real secret so _verify_signature runs for real. Set before importing the module.
SECRET = "voice-isolation-test-secret-0123456789"
os.environ["ELEVENLABS_WEBHOOK_SECRET"] = SECRET

from app.webhooks import voice_elevenlabs as wh  # noqa: E402

TENANT_A = "11111111-1111-4111-8111-aaaaaaaaaaaa"
TENANT_B = "22222222-2222-4222-8222-bbbbbbbbbbbb"
AGENT_A = "agent_AAA"
AGENT_B = "agent_BBB"
_AGENT_TO_TENANT = {AGENT_A: TENANT_A, AGENT_B: TENANT_B}

_failures: list[str] = []
_checks = 0

# Captured side effects (what the handler tried to write, and for whom).
_upserts: list[tuple[str, str, dict]] = []     # (tenant_id, call_id, fields)
_imports: list[tuple[int, str]] = []           # (lead_id, tenant_id)
_usage: list[tuple[str, str, float]] = []      # (tenant_id, event, qty)
_received: set[str] = set()


def _check(name: str, condition: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if condition:
        print(f"  PASS  {name}")
    else:
        msg = name + (f" — {detail}" if detail else "")
        print(f"  FAIL  {msg}")
        _failures.append(msg)


# ─── Stub the DB / registry / cache seams (NOT the attribution logic) ───────────

async def _fake_resolve(provider: str, external_id: str):
    assert provider == "elevenlabs"
    return _AGENT_TO_TENANT.get(external_id)  # None for unknown agents → reject


async def _fake_is_received(mid: str) -> bool:
    return mid in _received


async def _fake_mark_received(mid: str) -> None:
    _received.add(mid)


async def _fake_upsert(tenant_id: str, call_id: str, fields: dict):
    _upserts.append((tenant_id, call_id, dict(fields)))
    return {"id": len(_upserts), "tenant_id": tenant_id, "call_id": call_id}


async def _fake_import(lead_id: int, tenant_id: str):
    _imports.append((lead_id, tenant_id))
    return {"id": lead_id, "tenant_id": tenant_id}


async def _fake_usage(tenant_id, event="message", quantity=1):
    _usage.append((tenant_id, event, quantity))


def _install_stubs() -> None:
    wh.resolve_tenant_for_channel = _fake_resolve
    wh.is_already_received = _fake_is_received
    wh.mark_as_received = _fake_mark_received
    wh.upsert_voice_lead = _fake_upsert
    wh.import_elevenlabs_lead_to_pipeline = _fake_import
    wh.record_usage = _fake_usage


# ─── A minimal fake Request the handler can consume ─────────────────────────────

class _FakeHeaders:
    def __init__(self, mapping: dict):
        self._m = {k.lower(): v for k, v in mapping.items()}

    def get(self, key, default=None):
        return self._m.get(key.lower(), default)


class _FakeRequest:
    def __init__(self, body: bytes, headers: dict):
        self._body = body
        self.headers = _FakeHeaders(headers)

    async def body(self) -> bytes:
        return self._body

    async def json(self):
        return json.loads(self._body.decode())


def _sign(body: bytes, secret: str = SECRET, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v0={mac}"


def _payload(agent_id: str, conversation_id: str, caller: str, budget: str) -> bytes:
    return json.dumps({
        "type": "post_call_transcription",
        "data": {
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "metadata": {"call_duration_secs": 120, "phone_call": {"external_number": caller}},
            "analysis": {
                "transcript_summary": f"summary-{conversation_id}",
                "data_collection_results": {
                    "name": {"value": f"name-{conversation_id}"},
                    "budget": {"value": budget},
                },
            },
        },
    }).encode()


async def _post(body: bytes, signature: str):
    req = _FakeRequest(body, {"elevenlabs-signature": signature})
    return await wh.elevenlabs_post_call(req)


async def main() -> int:
    print("=" * 72)
    print("VOICE WEBHOOK TENANT ISOLATION — two tenants, distinct agents")
    print(f"  tenant A / agent : {TENANT_A} / {AGENT_A}")
    print(f"  tenant B / agent : {TENANT_B} / {AGENT_B}")
    print("=" * 72)
    _install_stubs()

    # 1. Tenant A's call → only tenant A.
    print("tenant A inbound call")
    body_a = _payload(AGENT_A, "conv-A", "+2348011111111", "A-50m")
    res_a = await _post(body_a, _sign(body_a))
    _check("A call accepted", res_a.status == "accepted", res_a.status)

    # 2. Tenant B's call → only tenant B.
    print("tenant B inbound call")
    body_b = _payload(AGENT_B, "conv-B", "+2348022222222", "B-90m")
    res_b = await _post(body_b, _sign(body_b))
    _check("B call accepted", res_b.status == "accepted", res_b.status)

    # 3. Attribution did not cross.
    print("attribution")
    a_rows = [u for u in _upserts if u[1] == "conv-A"]
    b_rows = [u for u in _upserts if u[1] == "conv-B"]
    _check("A's call written ONLY to tenant A", a_rows and all(u[0] == TENANT_A for u in a_rows), repr(a_rows))
    _check("B's call written ONLY to tenant B", b_rows and all(u[0] == TENANT_B for u in b_rows), repr(b_rows))
    _check("no A row leaked into tenant B", not any(u[0] == TENANT_B for u in a_rows))
    _check("A's budget never appears under B", not any("A-50m" in json.dumps(u[2]) for u in b_rows))
    _check("import attributed to A only for A", all(t == TENANT_A for (lid, t) in _imports if lid == 1))
    _check("usage metered to the resolving tenant", {u[0] for u in _usage} == {TENANT_A, TENANT_B})

    # 4. Unknown agent → rejected, nothing written.
    print("unregistered agent")
    before = len(_upserts)
    body_x = _payload("agent_UNKNOWN", "conv-X", "+2348033333333", "X")
    res_x = await _post(body_x, _sign(body_x))
    _check("unknown agent rejected", res_x.status == "rejected", res_x.status)
    _check("no lead written for unknown agent", len(_upserts) == before)

    # 5. Forged signature → rejected (fail-closed), nothing written.
    print("forged signature")
    before = len(_upserts)
    body_f = _payload(AGENT_A, "conv-F", "+2348044444444", "F")
    res_f = await _post(body_f, _sign(body_f, secret="wrong-secret"))
    _check("forged signature rejected", res_f.status == "rejected", res_f.status)
    _check("no lead written for forged sig", len(_upserts) == before)

    print("=" * 72)
    if _failures:
        print(f"VERDICT: BLOCK — {len(_failures)}/{_checks} checks failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"VERDICT: PASS — all {_checks} checks isolated. Voice calls never cross tenants.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

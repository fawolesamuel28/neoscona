"""Two-tenant proof for the Redis cache layer (Phase 3, Vuln 2).

Reva is a shared multi-tenant service. A lead's phone number is unique only
*within* a tenant (the (tenant_id, phone_number) constraint), so two businesses
may both talk to the same number. Every per-lead Redis key must therefore carry
the owning tenant, or one customer's conversation/profile/stage/matches would
collide with another's under the same phone.

This script drives the REAL service functions against a LIVE Redis and proves the
two tenants stay disjoint. It needs only Redis (REDIS_URL, default
redis://localhost:6379) — not Supabase — because the cache keys are the whole
surface under test.

    Run:   python scripts/verify_tenant_cache_isolation.py
    Exit:  0 = PASS (isolated), 1 = BLOCK (leak / unexpected error)

It uses a fresh random phone number per run and deletes every key it creates, so
it is safe to run against a shared dev Redis.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass  # dotenv is optional; REDIS_URL can come from the real environment

# Import the real code paths — we test genuine key construction, not a copy.
from app.cache.redis import (
    cache_lead_matches,
    get_cached_lead_matches,
    get_conversation_history,
    get_lead_data,
    get_lead_stage,
    get_redis_client,
    is_rate_limited,
    save_conversation_history,
    set_lead_stage,
    update_lead_data,
    close_redis_client,
)
from app.services.scoring import ScoringEngine

# Two distinct tenants that will converse with the SAME phone number.
TENANT_A = "11111111-1111-4111-8111-" + secrets.token_hex(6)
TENANT_B = "22222222-2222-4222-8222-" + secrets.token_hex(6)
# Fresh number per run so re-runs never collide (and rate-limit TTL can't bleed in).
PHONE = "+234" + str(secrets.randbelow(10**9)).zfill(9)

_failures: list[str] = []
_checks = 0


def _check(name: str, condition: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if condition:
        print(f"  PASS  {name}")
    else:
        msg = f"{name}" + (f" — {detail}" if detail else "")
        print(f"  FAIL  {msg}")
        _failures.append(msg)


async def _conversation_isolation() -> None:
    print("conversation history")
    await save_conversation_history(TENANT_A, PHONE, [{"role": "user", "content": "A-secret-budget-50m"}])
    await save_conversation_history(TENANT_B, PHONE, [{"role": "user", "content": "B-secret-budget-90m"}])

    hist_a = await get_conversation_history(TENANT_A, PHONE)
    hist_b = await get_conversation_history(TENANT_B, PHONE)

    _check("A reads only A's history", hist_a == [{"role": "user", "content": "A-secret-budget-50m"}], repr(hist_a))
    _check("B reads only B's history", hist_b == [{"role": "user", "content": "B-secret-budget-90m"}], repr(hist_b))
    _check("histories do not cross", hist_a != hist_b)


async def _lead_data_isolation() -> None:
    print("extracted lead data")
    await update_lead_data(TENANT_A, PHONE, {"budget": "A-50m", "location": "Lekki"})
    await update_lead_data(TENANT_B, PHONE, {"budget": "B-90m", "location": "Ikoyi"})

    data_a = await get_lead_data(TENANT_A, PHONE)
    data_b = await get_lead_data(TENANT_B, PHONE)

    _check("A reads only A's profile", data_a.get("budget") == "A-50m" and data_a.get("location") == "Lekki", repr(data_a))
    _check("B reads only B's profile", data_b.get("budget") == "B-90m" and data_b.get("location") == "Ikoyi", repr(data_b))


async def _stage_isolation() -> None:
    print("funnel stage")
    await set_lead_stage(TENANT_A, PHONE, "qualified")
    await set_lead_stage(TENANT_B, PHONE, "booking")

    stage_a = await get_lead_stage(TENANT_A, PHONE)
    stage_b = await get_lead_stage(TENANT_B, PHONE)

    _check("A's stage is independent", stage_a == "qualified", stage_a)
    _check("B's stage is independent", stage_b == "booking", stage_b)


async def _matches_isolation() -> None:
    print("property matches")
    await cache_lead_matches(TENANT_A, PHONE, [{"unit_id": "unit-A", "rank": 1}])
    await cache_lead_matches(TENANT_B, PHONE, [{"unit_id": "unit-B", "rank": 1}])

    m_a = await get_cached_lead_matches(TENANT_A, PHONE)
    m_b = await get_cached_lead_matches(TENANT_B, PHONE)

    _check("A reads only A's matches", m_a == [{"unit_id": "unit-A", "rank": 1}], repr(m_a))
    _check("B reads only B's matches", m_b == [{"unit_id": "unit-B", "rank": 1}], repr(m_b))


async def _rate_limit_isolation() -> None:
    print("rate limiting")
    # Exhaust A's per-minute budget (>10 hits) …
    a_limited = False
    for _ in range(12):
        a_limited = await is_rate_limited(TENANT_A, PHONE)
    # … B, on the same number, must still have a clean counter.
    b_limited_first = await is_rate_limited(TENANT_B, PHONE)

    _check("A is rate-limited after a burst", a_limited is True)
    _check("B is unaffected by A's burst", b_limited_first is False)


async def _scoring_isolation() -> None:
    print("behavioral scoring state (msg_count / last_msg_time)")
    # Bump A's volume several times, then B once. The counters live under separate
    # tenant-namespaced keys, so B's must read as a first message, not A's running total.
    for _ in range(5):
        await ScoringEngine.calculate_behavioural_score(PHONE, "interested in a viewing", tenant_id=TENANT_A)
    await ScoringEngine.calculate_behavioural_score(PHONE, "hello", tenant_id=TENANT_B)

    client = await get_redis_client()
    a_count = await client.get(f"reva:t:{TENANT_A}:lead:{PHONE}:msg_count")
    b_count = await client.get(f"reva:t:{TENANT_B}:lead:{PHONE}:msg_count")

    _check("A accumulated its own volume", a_count == "5", f"a_count={a_count!r}")
    _check("B's volume is independent (==1)", b_count == "1", f"b_count={b_count!r}")


async def _no_unnamespaced_keys() -> None:
    """Direct runtime guard: every Redis key touching this phone must be tenant-namespaced."""
    print("namespace guard (scan all keys for this phone)")
    client = await get_redis_client()
    keys = [k async for k in client.scan_iter(match=f"*{PHONE}*")]
    offenders = [k for k in keys if ":t:" not in k]
    _check(
        "no per-lead key lacks a tenant segment",
        not offenders,
        f"un-namespaced keys: {offenders}",
    )
    print(f"        (scanned {len(keys)} keys for {PHONE}, all tenant-scoped)")


async def _cleanup() -> None:
    client = await get_redis_client()
    keys = [k async for k in client.scan_iter(match=f"*{PHONE}*")]
    if keys:
        await client.delete(*keys)
    print(f"cleanup: removed {len(keys)} test keys")


async def main() -> int:
    print("=" * 72)
    print("TENANT CACHE ISOLATION — two tenants, one shared phone number")
    print(f"  REDIS_URL : {os.getenv('REDIS_URL', 'redis://localhost:6379')}")
    print(f"  tenant A  : {TENANT_A}")
    print(f"  tenant B  : {TENANT_B}")
    print(f"  phone     : {PHONE}  (shared by both)")
    print("=" * 72)

    # Fail fast with a clear message if Redis isn't reachable.
    try:
        client = await get_redis_client()
        await client.ping()
    except Exception as exc:  # noqa: BLE001 — surface any connection error plainly
        print(f"\nERROR: cannot reach Redis ({exc}).")
        print("Start Redis (or set REDIS_URL) and re-run.")
        return 1

    try:
        await _conversation_isolation()
        await _lead_data_isolation()
        await _stage_isolation()
        await _matches_isolation()
        await _rate_limit_isolation()
        await _scoring_isolation()
        await _no_unnamespaced_keys()
    finally:
        await _cleanup()
        await close_redis_client()

    print("=" * 72)
    if _failures:
        print(f"VERDICT: BLOCK — {len(_failures)}/{_checks} checks failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"VERDICT: PASS — all {_checks} checks isolated. No cross-tenant cache leakage.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

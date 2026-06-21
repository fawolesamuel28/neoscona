"""Channel registry — maps an inbound channel to its owning tenant.

An inbound message carries only the *sender's* identity (their phone number /
chat id). It does NOT say which of our customers it belongs to. The answer is the
**business inbox it arrived on**: a WhatsApp business number, an Instagram
account, a voice receptionist agent. This module resolves that.

`resolve_tenant_for_channel(provider, external_id)` looks up the `channels` row for
`(provider, external_id)` and returns the owning `tenant_id`, or **None** when no
active channel matches. The gateway treats None as a hard reject — we never guess a
tenant, because guessing wrong files one business's lead under another's workspace.

The `channels` table is created by the Phase 3 migration (with RLS). Until it is
applied this resolver tolerates the table being absent: the lookup raises, we log a
warning and return None, so inbound is *rejected* rather than crashing or leaking.

Expected table shape (Phase 3):
    channels(id uuid pk, tenant_id uuid not null, provider text not null,
             external_id text not null, active boolean default true,
             created_at timestamptz default now(),
             unique (provider, external_id))
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from app.db.supabase import get_supabase

logger = logging.getLogger(__name__)

# Resolution runs on every inbound message, so cache the (provider, external_id) ->
# tenant_id mapping briefly to avoid a DB round-trip per message. Channels change
# rarely; a short TTL keeps newly-provisioned channels live within a minute. We use
# time.monotonic() (immune to wall-clock jumps).
_CACHE_TTL_SECONDS = 60.0
_cache: dict[tuple[str, str], tuple[float, str | None]] = {}


def _cache_get(key: tuple[str, str]) -> str | None | object:
    """Return the cached tenant_id (which may be None), or the _MISS sentinel."""
    entry = _cache.get(key)
    if entry is None:
        return _MISS
    expires_at, value = entry
    if time.monotonic() >= expires_at:
        _cache.pop(key, None)
        return _MISS
    return value


def _cache_put(key: tuple[str, str], value: str | None) -> None:
    _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, value)


_MISS = object()


def clear_cache() -> None:
    """Drop the resolution cache (used by tests / after provisioning a channel)."""
    _cache.clear()


# Providers we accept a channel registration for. Mirrors the webhook parsers'
# `channel_provider` values (see app/models/lead.py).
#
# `elevenlabs` is the voice receptionist: external_id is the tenant's ConvAI
# agent_id, registered by the voice provisioning path (which calls register_channel
# directly — a trusted server path, like onboarding seed). The post-call webhook
# resolves the owning tenant from (elevenlabs, agent_id). It is intentionally NOT
# self-registerable via POST /api/channels: verify_channel_ownership has no verifier
# for it and returns False, so a tenant cannot claim another's agent_id.
KNOWN_PROVIDERS = frozenset(
    {"cloud", "360dialog", "evolution", "instagram", "vapi", "elevenlabs"}
)


class ChannelConflict(Exception):
    """A (provider, external_id) is already registered to a DIFFERENT tenant.

    The channels table enforces global uniqueness on (provider, external_id): one
    business inbox maps to exactly one tenant. Re-registering it elsewhere would let
    one customer hijack another's inbound, so registration refuses rather than steals.
    """


def _channel_verification_disabled() -> bool:
    """Dev-only bypass for provider ownership checks, mirroring auth's AUTH_DISABLED.

    Ignored in production. Lets the self-serve onboarding flow be exercised locally
    before the Meta / provider integrations are wired in.
    """
    env = (os.getenv("ENVIRONMENT") or "development").lower()
    disabled = (os.getenv("CHANNEL_VERIFICATION_DISABLED") or "").lower() in ("1", "true", "yes")
    if disabled and env == "production":
        logger.error("CHANNEL_VERIFICATION_DISABLED is set but ignored because ENVIRONMENT=production.")
        return False
    return disabled


async def verify_channel_ownership(
    tenant_id: str, provider: str, external_id: str, proof: str | None = None
) -> bool:
    """Prove the caller actually controls (provider, external_id) at the provider,
    BEFORE binding it to their tenant.

    This is the guard against cross-tenant inbox capture: the channels row is the
    SOLE authority the gateway uses to attribute inbound to a tenant, and inbound is
    signed with a shared, server-side provider secret (e.g. the Meta app secret). So
    nothing else stops one tenant from registering another's phone_number_id and
    having the victim's genuine, validly-signed messages routed to them. Self-serve
    registration MUST pass this check.

    NOT YET WIRED for any live provider — returns False (refuse) so a self-serve
    caller cannot claim an inbox they don't own. Plug provider checks in here when
    credentials are available:

      • cloud / instagram (Meta): exchange the Embedded-Signup `proof` code for the
        caller's WABA/IG access token, then confirm `external_id` (phone_number_id /
        IG account id) belongs to it via the Graph API. Requires the Meta app creds.
      • evolution: confirm the instance is one this server provisioned for the tenant.

    Trusted server paths (migration seed SQL, operator provisioning) call
    register_channel() directly and intentionally skip this check.
    """
    if _channel_verification_disabled():
        logger.warning(
            "Channel ownership verification BYPASSED (dev) for provider=%s external_id=%s tenant=%s",
            provider, external_id, tenant_id,
        )
        return True

    # No live provider verifier is wired yet — refuse to bind an unproven channel.
    logger.warning(
        "No ownership verifier for provider=%s; refusing self-serve registration "
        "(external_id=%s tenant=%s). Provision via operator/seed until verification is wired.",
        provider, external_id, tenant_id,
    )
    return False


async def register_channel(
    tenant_id: str, provider: str, external_id: str, *, active: bool = True
) -> dict:
    """Register (or re-activate) a channel→tenant mapping for inbound resolution.

    Idempotent for the OWNING tenant: re-registering the same (provider, external_id)
    updates `active`. Raises ``ChannelConflict`` if the channel already belongs to a
    different tenant, and ``ValueError`` for an unknown provider / blank id.

    Invalidates the resolution cache so the mapping is live immediately rather than
    after the TTL. Returns the persisted channels row.
    """
    if provider not in KNOWN_PROVIDERS:
        raise ValueError(f"unknown channel provider: {provider!r}")
    external_id = (external_id or "").strip()
    if not external_id:
        raise ValueError("external_id is required")
    if not tenant_id:
        raise ValueError("tenant_id is required")

    row = await asyncio.to_thread(_register, tenant_id, provider, external_id, active)
    # Drop any cached (provider, external_id) lookup — including a prior None miss —
    # so the next inbound resolves to the freshly-registered tenant.
    _cache.pop((provider, external_id), None)
    return row


def _register(tenant_id: str, provider: str, external_id: str, active: bool) -> dict:
    """Synchronous insert/update (run via to_thread). Service-role: bypasses RLS, so
    the cross-tenant ownership check below is the only guard against inbox hijack."""
    db = get_supabase()
    existing = (
        db.table("channels")
        .select("id, tenant_id")
        .eq("provider", provider)
        .eq("external_id", external_id)
        .limit(1)
        .execute()
    )
    rows = existing.data or []
    if rows:
        owner = rows[0]["tenant_id"]
        if owner != tenant_id:
            logger.warning(
                "Refusing to register channel provider=%s external_id=%s for tenant=%s: "
                "already owned by tenant=%s",
                provider, external_id, tenant_id, owner,
            )
            raise ChannelConflict(
                f"channel ({provider}, {external_id}) is already registered to another workspace"
            )
        res = (
            db.table("channels")
            .update({"active": active})
            .eq("id", rows[0]["id"])
            .execute()
        )
        return (res.data or [{}])[0]

    res = (
        db.table("channels")
        .insert(
            {
                "tenant_id": tenant_id,
                "provider": provider,
                "external_id": external_id,
                "active": active,
            }
        )
        .execute()
    )
    logger.info(
        "Registered channel provider=%s external_id=%s -> tenant=%s",
        provider, external_id, tenant_id,
    )
    return (res.data or [{}])[0]


async def resolve_tenant_for_channel(provider: str | None, external_id: str | None) -> str | None:
    """
    Resolve the tenant that owns the channel a message arrived on.

    Returns the owning `tenant_id`, or **None** when the channel is unknown (no
    active row, missing identifiers, or the table doesn't exist yet). Callers must
    treat None as "reject" — never fall back to a default tenant.
    """
    if not provider or not external_id:
        # Nothing to look up. A provider whose payload carries no business id must
        # supply one via the webhook URL (?channel=...); without it we can't tell
        # whose inbox this is.
        logger.warning(
            "Channel resolution skipped: provider=%r external_id=%r (no identifier).",
            provider, external_id,
        )
        return None

    key = (provider, external_id)
    cached = _cache_get(key)
    if cached is not _MISS:
        return cached  # type: ignore[return-value]

    tenant_id = await asyncio.to_thread(_lookup, provider, external_id)
    _cache_put(key, tenant_id)
    return tenant_id


def _lookup(provider: str, external_id: str) -> str | None:
    """Synchronous Supabase lookup (run via to_thread). Service-role: a trusted,
    pre-auth lookup, same posture as onboarding/entitlements tenant lookups."""
    try:
        db = get_supabase()
        res = (
            db.table("channels")
            .select("tenant_id")
            .eq("provider", provider)
            .eq("external_id", external_id)
            .eq("active", True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            logger.warning(
                "No active channel for provider=%s external_id=%s — rejecting inbound.",
                provider, external_id,
            )
            return None
        return rows[0]["tenant_id"]
    except Exception as exc:
        # Table absent (pre-Phase-3) or any lookup failure: reject, don't guess.
        logger.warning(
            "Channel lookup failed for provider=%s external_id=%s (rejecting): %s",
            provider, external_id, exc,
        )
        return None

"""Self-serve voice receptionist provisioning (multi-tenant).

Ties ElevenLabs (the agent/brain) and KrosAI (the number + routing) into one
idempotent, rollback-safe operation, and persists a `voice_agents` row scoped to the
tenant. The persona can be **dedicated** (a standalone voice-only setup) or derived
from the tenant's existing **Reva** agent config so voice and WhatsApp stay consistent.

Flow (see krosai.py for why this needs no raw SIP):
    create ElevenLabs agent → create KrosAI `elevenlabs` endpoint(agent_id)
      → purchase KrosAI number attached to that endpoint
      → register channel (elevenlabs, agent_id) → tenant   [inbound attribution]
      → persist voice_agents row.

Tenant scoping is the caller's job to *resolve*; every DB write here carries the
explicit `tenant_id` (service-role client, like the rest of provisioning/onboarding).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.tenant import require_tenant
from app.db.supabase import get_supabase
from app.services import agent_config as agent_config_service
from app.services.channels import register_channel
from app.services.voice import elevenlabs, krosai

logger = logging.getLogger(__name__)

# Reva config language label -> ElevenLabs/ASR 2-letter code (best-effort; defaults en).
_LANG_CODE = {
    "english": "en",
    "nigerian english": "en",
    "pidgin": "en",
    "yoruba": "yo",
    "igbo": "ig",
    "hausa": "ha",
    "french": "fr",
    "arabic": "ar",
}


class Persona(dict):
    """Resolved voice agent persona: name, prompt, first_message, language, voice_id."""


def _lang_code(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else "english"
    return _LANG_CODE.get(str(value or "english").strip().lower(), "en")


def build_persona_from_form(form: dict[str, Any]) -> Persona:
    """Persona for a dedicated, voice-only setup (standalone customers)."""
    name = (form.get("name") or "Reva Voice Receptionist").strip()[:120]
    company = (form.get("company_name") or "").strip()
    greeting = (form.get("greeting") or "").strip()
    prompt = (form.get("prompt") or "").strip()
    if not prompt:
        prompt = (
            f"You are {name}, a friendly, professional phone receptionist"
            + (f" for {company}" if company else "")
            + ". Greet the caller warmly, understand what they need, answer concisely, "
            "and capture their name, phone number, and the reason for their call. "
            "Keep responses short and natural for a voice conversation."
        )
    return Persona(
        name=name,
        prompt=prompt,
        first_message=greeting,
        language=(form.get("language") or "en").strip().lower()[:5],
        voice_id=(form.get("voice_id") or "").strip() or None,
    )


async def build_persona_from_reva(tenant_id: str) -> Persona:
    """Persona derived from the tenant's Reva agent config (consistent with WhatsApp)."""
    cfg = await agent_config_service.get_agent_config(tenant_id)
    agent_name = cfg.get("agent_name") or "Amara"
    company = cfg.get("company_name") or ""
    tone = cfg.get("tone") or ""
    qualifying = cfg.get("qualifying_fields") or ["budget", "location", "property_type", "timeline"]
    greeting = cfg.get("greeting") or ""
    qual_text = ", ".join(qualifying)
    prompt = (
        f"You are {agent_name}, a senior property consultant at {company} in Lagos, "
        "answering inbound phone calls. This is a live VOICE call, so keep every reply "
        "short, warm and natural — no lists, no markdown, no email sign-offs.\n\n"
        f"Tone: {tone}\n\n"
        "Goal: warmly greet the caller, understand what property they're after, and "
        f"naturally qualify them by collecting: {qual_text}. Also capture their name and "
        "a WhatsApp number for follow-up. If they're a serious buyer, offer to book a "
        "viewing or have a human consultant call them back. Never claim to be a robot."
    )
    return Persona(
        name=f"{agent_name} (Voice)"[:120],
        prompt=prompt,
        first_message=greeting,
        language=_lang_code(cfg.get("languages")),
        voice_id=None,
    )


# ─── voice_agents persistence (service-role, explicit tenant_id) ────────────────

_VOICE_FIELDS = (
    "id, tenant_id, provider, elevenlabs_agent_id, krosai_phone_id, e164, label, "
    "status, persona_source, config, created_at, updated_at"
)


async def get_voice_agent(tenant_id: str) -> Optional[dict[str, Any]]:
    """The tenant's most-recent voice receptionist row (any status), or None."""
    tenant_id = require_tenant(tenant_id)
    db = get_supabase()

    def _get():
        return (
            db.table("voice_agents")
            .select(_VOICE_FIELDS)
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

    try:
        res = await asyncio.to_thread(_get)
        return (res.data or [None])[0]
    except Exception as exc:
        logger.error("get_voice_agent failed for tenant %s: %s", tenant_id, exc)
        return None


async def _save_voice_agent(tenant_id: str, **fields: Any) -> dict[str, Any]:
    db = get_supabase()
    row = {"tenant_id": tenant_id, "updated_at": datetime.now(timezone.utc).isoformat(), **fields}

    def _insert():
        return db.table("voice_agents").insert(row).execute()

    res = await asyncio.to_thread(_insert)
    return (res.data or [row])[0]


async def _set_status(voice_agent_id: str, status: str) -> None:
    db = get_supabase()

    def _update():
        return (
            db.table("voice_agents")
            .update({"status": status, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", voice_agent_id)
            .execute()
        )

    await asyncio.to_thread(_update)


# ─── Orchestration ──────────────────────────────────────────────────────────────

async def provision_receptionist(
    tenant_id: str,
    *,
    persona: Persona,
    inventory_id: str,
    persona_source: str = "dedicated",
    label: Optional[str] = None,
    country: Optional[str] = None,
) -> dict[str, Any]:
    """Provision (or return the existing active) voice receptionist for a tenant.

    Idempotent: if the tenant already has an active receptionist, it is returned
    unchanged. On any partial failure, every external resource created during this
    call is rolled back so a retry starts clean.
    """
    tenant_id = require_tenant(tenant_id)
    existing = await get_voice_agent(tenant_id)
    if existing and existing.get("status") == "active":
        logger.info("Tenant %s already has an active voice receptionist; returning it.", tenant_id)
        return existing

    agent_id: Optional[str] = None
    endpoint_id: Optional[str] = None
    phone_id: Optional[str] = None
    try:
        agent_id = await elevenlabs.create_agent(
            name=persona["name"],
            prompt=persona["prompt"],
            first_message=persona.get("first_message") or "",
            language=persona.get("language") or "en",
            voice_id=persona.get("voice_id"),
        )
        endpoint = await krosai.create_elevenlabs_endpoint(
            name=f"reva-{tenant_id[:8]}", elevenlabs_agent_id=agent_id
        )
        endpoint_id = endpoint.get("id")
        if not endpoint_id:
            raise krosai.KrosAIError(f"endpoint create returned no id: {endpoint}")

        number = await krosai.purchase_number(inventory_id=inventory_id, endpoint_id=endpoint_id)
        phone_id = number.get("id")
        e164 = number.get("e164")
        if not phone_id:
            raise krosai.KrosAIError(f"number purchase returned no id: {number}")

        # Bind inbound attribution BEFORE marking active: the post-call webhook resolves
        # the tenant from (elevenlabs, agent_id) and must never fall back to a default.
        await register_channel(tenant_id, "elevenlabs", agent_id)

        row = await _save_voice_agent(
            tenant_id,
            provider="elevenlabs",
            elevenlabs_agent_id=agent_id,
            krosai_phone_id=phone_id,
            e164=e164,
            label=(label or persona["name"])[:120],
            status="active",
            persona_source=persona_source,
            config={
                "krosai_endpoint_id": endpoint_id,
                "voice_id": persona.get("voice_id"),
                "language": persona.get("language"),
                "first_message": persona.get("first_message"),
                "country": country,
            },
        )
        logger.info(
            "Voice receptionist provisioned for tenant %s: agent=%s number=%s (%s)",
            tenant_id, agent_id, phone_id, e164,
        )
        return row

    except Exception as exc:
        logger.error("Voice provisioning failed for tenant %s, rolling back: %s", tenant_id, exc)
        if phone_id:
            await krosai.release_number(phone_id)
        if endpoint_id:
            await krosai.delete_endpoint(endpoint_id)
        if agent_id:
            await elevenlabs.delete_agent(agent_id)
        raise


async def deprovision_receptionist(tenant_id: str) -> bool:
    """Tear down the tenant's voice receptionist: release number, delete endpoint+agent,
    deactivate the channel, and mark the row disabled. Returns True if one existed."""
    tenant_id = require_tenant(tenant_id)
    row = await get_voice_agent(tenant_id)
    if not row or row.get("status") == "disabled":
        return False

    agent_id = row.get("elevenlabs_agent_id")
    phone_id = row.get("krosai_phone_id")
    endpoint_id = (row.get("config") or {}).get("krosai_endpoint_id")

    if phone_id:
        await krosai.release_number(phone_id)
    if endpoint_id:
        await krosai.delete_endpoint(endpoint_id)
    if agent_id:
        await elevenlabs.delete_agent(agent_id)
        # Deactivate inbound attribution so a released number can't route to a dead agent.
        await register_channel(tenant_id, "elevenlabs", agent_id, active=False)

    await _set_status(row["id"], "disabled")
    logger.info("Voice receptionist deprovisioned for tenant %s (agent=%s)", tenant_id, agent_id)
    return True

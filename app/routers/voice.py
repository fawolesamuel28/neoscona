"""Voice receptionist provisioning API.

Self-serve endpoints for a tenant to stand up (and tear down) an AI voice
receptionist: browse KrosAI number inventory, provision a receptionist (ElevenLabs
agent + KrosAI number), view its status, update the persona, and disable it.

All routes are owner/admin only and gated behind the `voice` plan feature. The tenant
is ALWAYS taken from the authenticated principal — never the request body — so a caller
can only provision for their own workspace.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.auth import Principal
from app.core.entitlements import require_feature
from app.services.voice import elevenlabs, krosai
from app.services.voice.provisioning import (
    build_persona_from_form,
    build_persona_from_reva,
    deprovision_receptionist,
    get_voice_agent,
    provision_receptionist,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Voice Receptionist"])

# Owner/admin with the `voice` plan feature (402 + upgrade hint otherwise).
require_voice = require_feature("voice")


class DedicatedPersona(BaseModel):
    name: str = Field(default="Voice Receptionist", max_length=120)
    company_name: Optional[str] = Field(default=None, max_length=120)
    greeting: Optional[str] = Field(default=None, max_length=500, description="First line spoken on pickup")
    prompt: Optional[str] = Field(default=None, max_length=4000)
    voice_id: Optional[str] = None
    language: str = Field(default="en", max_length=5)


class ProvisionBody(BaseModel):
    inventory_id: str = Field(min_length=1, description="A KrosAI available-number inventory id")
    persona_source: str = Field(default="dedicated", description="'reva' | 'dedicated'")
    country: Optional[str] = None
    label: Optional[str] = Field(default=None, max_length=120)
    dedicated: Optional[DedicatedPersona] = Field(
        default=None, description="Required when persona_source='dedicated'"
    )


class PersonaUpdateBody(BaseModel):
    prompt: Optional[str] = Field(default=None, max_length=4000)
    greeting: Optional[str] = Field(default=None, max_length=500)
    voice_id: Optional[str] = None
    language: Optional[str] = Field(default=None, max_length=5)


def _public_voice_agent(row: Optional[dict]) -> Optional[dict]:
    """Strip internal provider ids before returning a voice agent to the client."""
    if not row:
        return None
    cfg = row.get("config") or {}
    return {
        "id": row.get("id"),
        "status": row.get("status"),
        "e164": row.get("e164"),
        "label": row.get("label"),
        "persona_source": row.get("persona_source"),
        "language": cfg.get("language"),
        "voice_id": cfg.get("voice_id"),
        "created_at": row.get("created_at"),
    }


@router.get("/voice/numbers/available")
async def available_numbers(
    country: str = Query(min_length=2, max_length=2, description="ISO country code, e.g. NG"),
    number_type: str = Query("local", alias="type"),
    principal: Principal = Depends(require_voice),
):
    """Proxy KrosAI's available-number inventory for the purchase picker."""
    try:
        numbers = await krosai.list_available_numbers(country, number_type=number_type)
    except krosai.KrosAIError as exc:
        raise HTTPException(status_code=502, detail=f"KrosAI inventory lookup failed: {exc}")
    return {"numbers": numbers}


@router.get("/voice/receptionist")
async def get_receptionist(principal: Principal = Depends(require_voice)):
    """The caller's current voice receptionist (status + number), or null."""
    return {"receptionist": _public_voice_agent(await get_voice_agent(principal.tenant_id))}


@router.post("/voice/receptionist")
async def create_receptionist(
    body: ProvisionBody,
    principal: Principal = Depends(require_voice),
):
    """Provision a voice receptionist for the caller's workspace.

    `persona_source='reva'` reuses the tenant's Amara agent config; `'dedicated'`
    uses the supplied `dedicated` form (for voice-only customers).
    """
    if body.persona_source == "reva":
        persona = await build_persona_from_reva(principal.tenant_id)
    elif body.persona_source == "dedicated":
        form = (body.dedicated or DedicatedPersona()).model_dump()
        persona = build_persona_from_form(form)
    else:
        raise HTTPException(status_code=422, detail="persona_source must be 'reva' or 'dedicated'")

    try:
        row = await provision_receptionist(
            principal.tenant_id,
            persona=persona,
            inventory_id=body.inventory_id,
            persona_source=body.persona_source,
            label=body.label,
            country=body.country,
        )
    except krosai.KrosAIError as exc:
        # Surface purchase/KYC/balance failures as a 402 the UI can act on.
        raise HTTPException(status_code=402, detail=f"Number provisioning failed: {exc}")
    except elevenlabs.ElevenLabsError as exc:
        raise HTTPException(status_code=502, detail=f"Voice agent creation failed: {exc}")
    return {"receptionist": _public_voice_agent(row)}


@router.patch("/voice/receptionist")
async def update_receptionist(
    body: PersonaUpdateBody,
    principal: Principal = Depends(require_voice),
):
    """Update the receptionist's persona/voice (patches the live ElevenLabs agent)."""
    row = await get_voice_agent(principal.tenant_id)
    if not row or row.get("status") == "disabled":
        raise HTTPException(status_code=404, detail="No active voice receptionist")
    agent_id = row.get("elevenlabs_agent_id")
    if not agent_id:
        raise HTTPException(status_code=409, detail="Receptionist is not fully provisioned")
    try:
        await elevenlabs.update_agent(
            agent_id,
            prompt=body.prompt,
            first_message=body.greeting,
            language=body.language,
            voice_id=body.voice_id,
        )
    except elevenlabs.ElevenLabsError as exc:
        raise HTTPException(status_code=502, detail=f"Voice agent update failed: {exc}")
    return {"receptionist": _public_voice_agent(row)}


@router.delete("/voice/receptionist")
async def delete_receptionist(principal: Principal = Depends(require_voice)):
    """Disable the receptionist: release the number, delete the agent + endpoint."""
    removed = await deprovision_receptionist(principal.tenant_id)
    if not removed:
        raise HTTPException(status_code=404, detail="No active voice receptionist")
    return {"status": "disabled"}

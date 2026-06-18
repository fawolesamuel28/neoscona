"""Per-tenant AI agent configuration endpoints.

GET is readable by any member (drives the settings page + a config preview); PUT requires
admin. The persona is merged over code defaults, so the response always returns a complete,
usable config even before a tenant has saved anything.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.auth import Principal, get_current_principal, require_role
from app.services.agent_config import (
    QUALIFYING_CHOICES,
    get_agent_config,
    upsert_agent_config,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Agent Config"])


class AgentConfigBody(BaseModel):
    agent_name: Optional[str] = Field(default=None, max_length=80)
    company_name: Optional[str] = Field(default=None, max_length=120)
    tone: Optional[str] = Field(default=None, max_length=2000)
    languages: Optional[list[str]] = None
    qualifying_fields: Optional[list[str]] = None
    guardrails: Optional[str] = Field(default=None, max_length=2000)
    custom_instructions: Optional[str] = Field(default=None, max_length=2000)
    greeting: Optional[str] = Field(default=None, max_length=400)


@router.get("/agent-config")
async def read_agent_config(principal: Principal = Depends(get_current_principal)):
    """Resolved persona config for the caller's tenant (merged over defaults)."""
    cfg = await get_agent_config(principal.tenant_id)
    return {"config": cfg, "qualifying_choices": list(QUALIFYING_CHOICES)}


@router.put("/agent-config")
async def write_agent_config(
    body: AgentConfigBody,
    principal: Principal = Depends(require_role("admin")),
):
    """Save persona config (admin+). Returns the merged, resolved config."""
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    cfg = await upsert_agent_config(principal.tenant_id, patch)
    return {"config": cfg}

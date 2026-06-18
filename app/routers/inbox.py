"""Shared inbox + human-takeover endpoints.

Reads/writes require `agent` role or higher; reassigning a lead to another teammate
requires `admin`. Every write first verifies the phone belongs to the caller's tenant
(404 otherwise), mirroring the ownership checks in elevenlabs_leads.py.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.auth import Principal, require_role
from app.services import inbox as inbox_svc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Inbox"])


def _sla_minutes() -> int:
    try:
        return int(os.getenv("INBOX_SLA_MINUTES", "10"))
    except ValueError:
        return 10


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class NoteBody(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class TagsBody(BaseModel):
    tags: list[str] = Field(default_factory=list)


class AssignBody(BaseModel):
    user_id: str | None = None


@router.get("/inbox")
async def get_inbox(
    filter: str = Query(default="all"),
    principal: Principal = Depends(require_role("agent")),
):
    """Inbox leads for the caller's tenant (filter: mine|unassigned|takeover|all)."""
    leads = await inbox_svc.list_inbox(
        principal.tenant_id, principal, filter_=filter, sla_minutes=_sla_minutes()
    )
    return {"leads": leads, "total": len(leads), "filter": filter}


@router.post("/inbox/{phone}/takeover")
async def post_takeover(phone: str, principal: Principal = Depends(require_role("agent"))):
    lead = await inbox_svc.takeover(principal.tenant_id, phone, principal)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"lead": lead}


@router.post("/inbox/{phone}/handback")
async def post_handback(phone: str, principal: Principal = Depends(require_role("agent"))):
    lead = await inbox_svc.handback(principal.tenant_id, phone)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"lead": lead}


@router.post("/inbox/{phone}/reply")
async def post_reply(
    phone: str,
    body: ReplyBody,
    principal: Principal = Depends(require_role("agent")),
):
    ok = await inbox_svc.send_human_reply(principal.tenant_id, phone, principal, body.text)
    if not ok:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"status": "sent"}


@router.get("/inbox/{phone}/notes")
async def get_notes(phone: str, principal: Principal = Depends(require_role("agent"))):
    return {"notes": await inbox_svc.list_notes(principal.tenant_id, phone)}


@router.post("/inbox/{phone}/notes")
async def post_note(
    phone: str,
    body: NoteBody,
    principal: Principal = Depends(require_role("agent")),
):
    note = await inbox_svc.add_note(principal.tenant_id, phone, principal, body.body)
    if not note:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"note": note}


@router.put("/inbox/{phone}/tags")
async def put_tags(
    phone: str,
    body: TagsBody,
    principal: Principal = Depends(require_role("agent")),
):
    lead = await inbox_svc.set_tags(principal.tenant_id, phone, body.tags)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"lead": lead}


@router.post("/inbox/{phone}/assign")
async def post_assign(
    phone: str,
    body: AssignBody,
    principal: Principal = Depends(require_role("admin")),
):
    lead = await inbox_svc.assign(principal.tenant_id, phone, body.user_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"lead": lead}

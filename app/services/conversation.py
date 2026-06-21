from __future__ import annotations

import logging
from typing import Any

from app.cache.redis import (
    clear_conversation,
    get_conversation_history,
    get_lead_data,
    get_lead_stage,
    save_conversation_history,
    set_lead_stage,
    update_lead_data,
)
from app.models.lead import IncomingMessage
from app.core.tenant import require_tenant

logger = logging.getLogger(__name__)

from app.core.state_machine import LeadStage, transition, can_transition

# Ordered funnel stages
FUNNEL: list[str] = [s.value for s in LeadStage]

# Fields required to consider a lead fully qualified
REQUIRED_FIELDS: list[str] = ["budget", "location", "property_type", "timeline"]


# ---------------------------------------------------------------------------
# Context builder — called before every LLM inference
# ---------------------------------------------------------------------------

async def build_conversation_context(incoming: IncomingMessage, *, tenant_id: str) -> dict[str, Any]:
    """
    Assemble everything the LLM needs before it responds:
      - Full conversation history (with the new user message already appended)
      - Current funnel stage
      - Accumulated lead data extracted so far

    `tenant_id` (the channel-resolved tenant) scopes every cache read so a lead's
    history/stage/profile is never read from another workspace sharing the number.

    Returns a single context dict consumed by the AI handler.
    """
    tenant_id = require_tenant(tenant_id)
    phone = incoming.phone_number

    history = await get_conversation_history(tenant_id, phone)
    stage = await get_lead_stage(tenant_id, phone)
    lead_data = await get_lead_data(tenant_id, phone)

    # Append the incoming user turn
    history.append({"role": "user", "content": incoming.message})

    logger.debug(
        "Context built for %s | stage=%s | turns=%d | lead_data=%s",
        phone, stage, len(history), lead_data,
    )

    return {
        "phone_number": phone,
        "history": history,
        "stage": stage,
        "lead_data": lead_data,
        "message_id": incoming.message_id,
    }


# ---------------------------------------------------------------------------
# Response persistence — called after every successful AI response
# ---------------------------------------------------------------------------

async def save_ai_response(
    phone_number: str,
    history: list[dict[str, str]],
    ai_response: str,
    extracted_data: dict[str, Any] | None = None,
    *,
    tenant_id: str,
) -> None:
    """
    1. Append the assistant turn to history and save to Redis.
    2. Merge any newly extracted lead fields into the lead data cache.

    `tenant_id` scopes both cache writes to the owning workspace.
    """
    tenant_id = require_tenant(tenant_id)
    history.append({"role": "assistant", "content": ai_response})
    await save_conversation_history(tenant_id, phone_number, history)

    if extracted_data:
        await update_lead_data(tenant_id, phone_number, extracted_data)
        logger.debug("Lead data updated for %s: %s", phone_number, extracted_data)


# ---------------------------------------------------------------------------
# Stage advancement — call after each turn with the latest extracted data
# ---------------------------------------------------------------------------

async def advance_stage_if_ready(
    phone_number: str,
    current_stage: str,
    lead_data: dict[str, Any],
    *,
    tenant_id: str,
) -> str:
    """
    Evaluate whether the lead should move to the next funnel stage.

    Progression rules
    -----------------
    new        → qualifying   : always, on first message
    qualifying → qualified    : when all 4 required fields are collected
    qualified  → booking      : caller signals readiness (e.g. after summary sent)
    booking    → done         : caller signals meeting booked

    Returns the (possibly unchanged) new stage.
    """
    tenant_id = require_tenant(tenant_id)
    collected = [f for f in REQUIRED_FIELDS if lead_data.get(f)]

    if current_stage == LeadStage.NEW.value:
        next_stage = LeadStage.QUALIFYING.value

    elif current_stage == LeadStage.QUALIFYING.value:
        if len(collected) >= len(REQUIRED_FIELDS):
            next_stage = LeadStage.QUALIFIED.value
        else:
            next_stage = current_stage

    elif current_stage in (LeadStage.QUALIFIED.value, LeadStage.BOOKING.value):
        # AI-driven transitions
        next_stage = current_stage
    
    else:
        next_stage = current_stage

    if next_stage != current_stage:
        try:
            validated_stage = transition(current_stage, next_stage)
            await set_lead_stage(tenant_id, phone_number, validated_stage)
            logger.info(
                "Funnel advance: %s | %s → %s | collected=%s",
                phone_number, current_stage, validated_stage, collected,
            )
            return validated_stage
        except ValueError as e:
            logger.warning(f"Illegal transition attempted: {e}")
            return current_stage

    return next_stage


async def force_advance_stage(phone_number: str, *, tenant_id: str) -> str:
    """
    Explicitly move a lead one step forward in the funnel.
    Used by the AI handler when it determines the time is right
    (e.g. all data confirmed, or meeting link sent).
    """
    tenant_id = require_tenant(tenant_id)
    current = await get_lead_stage(tenant_id, phone_number)
    idx = FUNNEL.index(current) if current in FUNNEL else 0
    next_stage = FUNNEL[min(idx + 1, len(FUNNEL) - 1)]

    try:
        validated = transition(current, next_stage)
        await set_lead_stage(tenant_id, phone_number, validated)
        logger.info("Force advance: %s | %s → %s", phone_number, current, validated)
        return validated
    except ValueError as e:
        logger.warning(f"Illegal force transition: {e}")
        return current


async def reset_lead(phone_number: str, *, tenant_id: str) -> None:
    """
    Fully reset a lead — clears history, stage, and cached data.
    Useful for testing or when a lead explicitly asks to start over.
    """
    tenant_id = require_tenant(tenant_id)
    await clear_conversation(tenant_id, phone_number)
    await set_lead_stage(tenant_id, phone_number, LeadStage.NEW.value)
    logger.info("Lead reset: %s", phone_number)

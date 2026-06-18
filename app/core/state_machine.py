from enum import Enum
from typing import Set, Dict

class LeadStage(Enum):
    NEW = "new"
    QUALIFYING = "qualifying"
    QUALIFIED = "qualified"
    BOOKING = "booking"
    CONFIRMED = "confirmed"
    SITE_VISIT = "site_visit"
    OFFER = "offer"
    AGREEMENT = "agreement"
    PAYMENT = "payment"
    CLOSED = "closed"
    LOST = "lost"

VALID_TRANSITIONS: Dict[LeadStage, Set[LeadStage]] = {
    LeadStage.NEW: {LeadStage.QUALIFYING, LeadStage.LOST},
    LeadStage.QUALIFYING: {LeadStage.QUALIFIED, LeadStage.LOST},
    LeadStage.QUALIFIED: {LeadStage.BOOKING, LeadStage.LOST},
    LeadStage.BOOKING: {LeadStage.CONFIRMED, LeadStage.LOST},
    LeadStage.CONFIRMED: {LeadStage.SITE_VISIT, LeadStage.LOST},
    LeadStage.SITE_VISIT: {LeadStage.OFFER, LeadStage.LOST},
    LeadStage.OFFER: {LeadStage.AGREEMENT, LeadStage.LOST},
    LeadStage.AGREEMENT: {LeadStage.PAYMENT, LeadStage.LOST},
    LeadStage.PAYMENT: {LeadStage.CLOSED, LeadStage.LOST},
    LeadStage.CLOSED: set(),
    LeadStage.LOST: {LeadStage.QUALIFYING},  # Re-engagement allowed
}

def can_transition(current: LeadStage, next_stage: LeadStage) -> bool:
    """Check if a transition between two stages is valid."""
    return next_stage in VALID_TRANSITIONS.get(current, set())

def transition(current: str, next_stage: str) -> str:
    """
    Validates and performs a stage transition.
    Raises ValueError if the transition is illegal.
    """
    try:
        curr_enum = LeadStage(current.lower())
        next_enum = LeadStage(next_stage.lower())
    except ValueError:
        raise ValueError(f"Invalid stage name: {current} or {next_stage}")

    if not can_transition(curr_enum, next_enum):
        raise ValueError(
            f"Invalid transition: {curr_enum.value} → {next_enum.value}"
        )
    return next_enum.value

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class UnitStatus(str, Enum):
    available = "available"
    reserved = "reserved"
    sold = "sold"


class UnitMatch(BaseModel):
    unit_id: str
    unit_code: str
    title: str
    development_name: str
    phase: Optional[str] = None
    location: str
    property_type: str
    bedrooms: Optional[int] = None
    price_naira: int
    price_display: str
    highlights: Optional[str] = None
    payment_plan_notes: Optional[str] = None
    match_score: float = Field(ge=0, le=100)
    rank: int = Field(ge=1, le=3)


class LeadMatchRequest(BaseModel):
    budget: Optional[str] = None
    location: Optional[str] = None
    property_type: Optional[str] = None
    limit: int = Field(default=3, ge=1, le=5)

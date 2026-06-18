from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class Language(str, Enum):
    english = "english"
    pidgin = "pidgin"
    yoruba = "yoruba"
    igbo = "igbo"


class PropertyType(str, Enum):
    flat = "flat"
    duplex = "duplex"
    land = "land"
    commercial = "commercial"
    bungalow = "bungalow"


class IncomingMessage(BaseModel):
    phone_number: str
    message: str
    message_id: str
    message_type: str = "text"
    source: str = "whatsapp_organic"
    media_url: Optional[str] = None


class LeadProfile(BaseModel):
    phone_number: str
    name: Optional[str] = None
    budget: Optional[str] = None
    location: Optional[str] = None
    property_type: Optional[PropertyType] = None
    timeline: Optional[str] = None
    seriousness_score: Optional[int] = Field(default=None, ge=1, le=10)
    language: Language = Language.english
    notes: Optional[str] = None


class WebhookAckResponse(BaseModel):
    status: str = "ok"


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str

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

    # Channel identity — *which business inbox* received this message, used to
    # resolve the owning tenant (see app/services/channels.py). Set by the webhook
    # parser and kept distinct from `source`, which a "[source:facebook_ad]" tag can
    # overwrite for marketing attribution.
    channel_provider: Optional[str] = None  # cloud | 360dialog | evolution | instagram | vapi | elevenlabs
    channel_external_id: Optional[str] = None  # provider's business-account id (phone_number_id, IG id, instance, …)


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

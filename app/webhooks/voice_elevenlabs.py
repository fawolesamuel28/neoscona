"""ElevenLabs ConvAI post-call webhook — the producer of voice leads.

When a receptionist call ends, ElevenLabs POSTs a `post_call_transcription` event here
with the transcript, summary, and extracted data-collection fields. We:

  1. verify the HMAC `elevenlabs-signature` over the RAW body (fail-closed),
  2. resolve the owning tenant from the channel registry by `agent_id`
     (provider='elevenlabs') — never a default tenant,
  3. dedupe on `conversation_id` (webhooks retry),
  4. upsert an `elevenlabs_leads` row (tenant-scoped),
  5. meter voice minutes, and
  6. import the capture into the unified `leads` pipeline so existing follow-up runs.

The webhook is authenticated centrally by our shared secret; attribution to a tenant is
then by agent_id, mirroring `app/webhooks/gateway.py`'s "verify, then resolve" posture.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Optional

from fastapi import APIRouter, Request

from app.cache.redis import is_already_received, mark_as_received
from app.core.logger import get_logger
from app.models.lead import WebhookAckResponse
from app.services.channels import resolve_tenant_for_channel
from app.services.elevenlabs_leads import (
    import_elevenlabs_lead_to_pipeline,
    upsert_voice_lead,
)
from app.services.usage import record_usage

logger = get_logger(__name__)
router = APIRouter(tags=["Voice Webhook (ElevenLabs)"])

# Reject events whose signed timestamp is older than this (replay protection).
_MAX_SIGNATURE_AGE_SECONDS = 30 * 60


def _verify_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Verify the `elevenlabs-signature` header. FAIL-CLOSED.

    Header form: ``t=<unix_ts>,v0=<hex_hmac>``. The HMAC is SHA-256 over
    ``f"{t}.{raw_body}"`` using ELEVENLABS_WEBHOOK_SECRET. We compare in constant time
    and reject stale timestamps.
    """
    secret = (os.getenv("ELEVENLABS_WEBHOOK_SECRET") or "").strip()
    if not secret:
        logger.error("ELEVENLABS_WEBHOOK_SECRET unset — rejecting voice webhook (fail-closed).")
        return False
    if not signature_header:
        return False

    timestamp: Optional[str] = None
    provided: Optional[str] = None
    for part in signature_header.split(","):
        part = part.strip()
        if part.startswith("t="):
            timestamp = part[2:]
        elif part.startswith("v0="):
            provided = part[3:]
    if not timestamp or not provided:
        return False

    try:
        if abs(time.time() - int(timestamp)) > _MAX_SIGNATURE_AGE_SECONDS:
            logger.warning("Voice webhook signature timestamp too old — rejecting.")
            return False
    except ValueError:
        return False

    signed_payload = f"{timestamp}.{raw_body.decode('utf-8')}"
    expected = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def _collected(results: dict[str, Any], key: str) -> Optional[str]:
    """Pull a value out of `analysis.data_collection_results[key]`.

    Each entry is typically ``{"value": ..., "rationale": ...}`` but may be a bare
    scalar; tolerate both. Returns a string or None.
    """
    entry = (results or {}).get(key)
    if entry is None:
        return None
    if isinstance(entry, dict):
        value = entry.get("value")
    else:
        value = entry
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@router.post("/voice/elevenlabs", response_model=WebhookAckResponse)
async def elevenlabs_post_call(request: Request) -> WebhookAckResponse:
    """Receive ElevenLabs post-call events and route captures into the Reva pipeline."""
    raw = await request.body()
    if not _verify_signature(raw, request.headers.get("elevenlabs-signature")):
        # Match the gateway's posture: a bad signature is unauthenticated, not "ignored".
        return WebhookAckResponse(status="rejected")

    payload = await request.json()
    event_type = payload.get("type", "")
    data = payload.get("data", {}) or {}

    if event_type == "call_initiation_failure":
        logger.warning("ElevenLabs call initiation failure: %s", data.get("reason") or data)
        return WebhookAckResponse(status="accepted")

    if event_type != "post_call_transcription":
        # post_call_audio and any future types are not lead-bearing for us.
        return WebhookAckResponse(status="ignored")

    agent_id = data.get("agent_id")
    conversation_id = data.get("conversation_id")
    if not agent_id or not conversation_id:
        return WebhookAckResponse(status="ignored")

    # 1. Tenant resolution — the channel registry is the sole authority; never default.
    tenant_id = await resolve_tenant_for_channel("elevenlabs", agent_id)
    if not tenant_id:
        logger.warning(
            "Rejecting voice call %s: no tenant for elevenlabs agent_id=%s",
            conversation_id, agent_id,
        )
        return WebhookAckResponse(status="rejected")

    # 2. Dedup — webhooks retry; process a conversation_id once.
    if await is_already_received(f"el_{conversation_id}"):
        return WebhookAckResponse(status="duplicate")
    await mark_as_received(f"el_{conversation_id}")

    # 3. Extract caller + data-collection fields.
    metadata = data.get("metadata", {}) or {}
    phone_call = metadata.get("phone_call", {}) or {}
    caller = phone_call.get("external_number")
    analysis = data.get("analysis", {}) or {}
    results = analysis.get("data_collection_results", {}) or {}

    fields = {
        "name": _collected(results, "name"),
        "phone_number": _collected(results, "phone_number") or caller,
        "whatsapp_number": _collected(results, "whatsapp_number"),
        "budget": _collected(results, "budget"),
        "location": _collected(results, "location"),
        "property_type": _collected(results, "property_type"),
        "timeline": _collected(results, "timeline"),
        "ai_summary": analysis.get("transcript_summary"),
    }

    lead = await upsert_voice_lead(tenant_id, conversation_id, fields)

    # 4. Meter voice minutes (best-effort; never blocks the pipeline).
    duration = metadata.get("call_duration_secs") or 0
    if duration:
        await record_usage(tenant_id, "voice_minute", round(duration / 60.0, 2))

    # 5. Import into the unified leads pipeline → existing follow-up jobs take over.
    if lead and lead.get("id"):
        await import_elevenlabs_lead_to_pipeline(lead["id"], tenant_id)

    logger.info("Voice call %s captured for tenant %s", conversation_id, tenant_id)
    return WebhookAckResponse(status="accepted")

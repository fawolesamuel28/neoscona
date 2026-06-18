"""
Vapi.ai Voice Inbound Webhook.
Handles call lifecycle events: call.started, transcription updates, and call.ended.
First 2 minutes qualify the lead, then either auto-books or live-transfers.
"""
import os
from fastapi import APIRouter, Request
from app.core.logger import get_logger
from app.models.lead import IncomingMessage, WebhookAckResponse
from app.webhooks.gateway import gateway

logger = get_logger(__name__)
router = APIRouter(tags=["Vapi Voice Webhook"])

@router.post("/vapi", response_model=WebhookAckResponse)
async def vapi_webhook(request: Request) -> WebhookAckResponse:
    """
    Receives Vapi.ai call events and routes transcripts into the Reva pipeline.
    """
    payload = await request.json()
    event_type = payload.get("type") or payload.get("message", {}).get("type", "")

    # 1. Call Started — log and acknowledge
    if event_type == "call.started" or event_type == "assistant-request":
        call_id = payload.get("call", {}).get("id", "unknown")
        customer_phone = payload.get("call", {}).get("customer", {}).get("number", "")
        logger.info(f"Vapi call started: {call_id} from {customer_phone}")
        return WebhookAckResponse(status="accepted")

    # 2. Transcript / conversation-update — the main qualification path
    if event_type in ("transcript", "conversation-update", "end-of-call-report"):
        call_data = payload.get("call", {})
        customer_phone = call_data.get("customer", {}).get("number", "")
        call_id = call_data.get("id", "")

        # Extract transcript text
        transcript = ""
        if event_type == "transcript":
            transcript = payload.get("transcript", "")
        elif event_type == "conversation-update":
            messages = payload.get("messages", [])
            # Get the latest user message
            user_msgs = [m for m in messages if m.get("role") == "user"]
            if user_msgs:
                transcript = user_msgs[-1].get("content", "")
        elif event_type == "end-of-call-report":
            transcript = payload.get("summary", "")

        if not transcript or not customer_phone:
            return WebhookAckResponse(status="ignored")

        # Normalize phone (remove + prefix if present)
        customer_phone = customer_phone.lstrip("+")

        incoming = IncomingMessage(
            phone_number=customer_phone,
            message=f"[VOICE_CALL]: {transcript}",
            message_id=f"vapi_{call_id}_{event_type}",
            message_type="text",
            source="vapi"
        )

        return await gateway.ingest(request, incoming)

    # 3. Call Ended — log duration and outcome
    if event_type == "call.ended":
        call_id = payload.get("call", {}).get("id", "unknown")
        duration = payload.get("call", {}).get("duration", 0)
        logger.info(f"Vapi call ended: {call_id}, duration: {duration}s")
        return WebhookAckResponse(status="accepted")

    return WebhookAckResponse(status="ignored")

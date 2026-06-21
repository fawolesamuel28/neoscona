import os
from fastapi import APIRouter, Request, HTTPException
from app.core.logger import get_logger
from app.models.lead import IncomingMessage, WebhookAckResponse
from app.webhooks.gateway import gateway

logger = get_logger(__name__)
router = APIRouter(tags=["Instagram Webhook"])

@router.post("/instagram", response_model=WebhookAckResponse)
async def instagram_webhook(request: Request):
    """
    Handle Instagram DM webhooks from Meta Graph API.
    """
    # 1. Verification for Webhook Setup (GET request)
    # Meta sends a hub.verify_token to verify the webhook
    # This is handled separately or we can add it here if needed.

    # 2. Handle POST events
    payload = await request.json()
    
    if payload.get("object") != "instagram":
        return WebhookAckResponse(status="ignored")

    results = []
    for entry in payload.get("entry", []):
        # entry.id is the Instagram business account that received the DM — the
        # per-tenant channel key.
        ig_account_id = entry.get("id")
        for messaging_event in entry.get("messaging", []):
            if "message" in messaging_event and not messaging_event.get("message", {}).get("is_echo"):
                sender_id = messaging_event.get("sender", {}).get("id")
                message_id = messaging_event.get("message", {}).get("mid")
                text = messaging_event.get("message", {}).get("text", "")

                if not sender_id or not text:
                    continue

                incoming = IncomingMessage(
                    phone_number=sender_id,
                    message=text,
                    message_id=message_id,
                    message_type="text",
                    source="instagram",
                    channel_provider="instagram",
                    channel_external_id=ig_account_id,
                )

                # Ingest via gateway (don't return — process all messages in the batch)
                result = await gateway.ingest(request, incoming)
                results.append(result)

    if results:
        return results[-1]
    return WebhookAckResponse(status="ok")

@router.get("/instagram")
async def instagram_verification(request: Request):
    """
    Meta webhook verification handler.
    """
    params = request.query_params
    hub_mode = params.get("hub.mode")
    hub_verify_token = params.get("hub.verify_token")
    hub_challenge = params.get("hub.challenge")

    if hub_mode == "subscribe" and hub_verify_token == os.getenv("INSTAGRAM_VERIFY_TOKEN"):
        return int(hub_challenge)
    
    raise HTTPException(status_code=403, detail="Verification failed")

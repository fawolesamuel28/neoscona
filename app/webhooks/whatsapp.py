from __future__ import annotations

import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.logger import get_logger
from app.models.lead import IncomingMessage, WebhookAckResponse
from app.webhooks.gateway import gateway

logger = get_logger(__name__)
router = APIRouter(tags=["WhatsApp Webhook"])

# ---------------------------------------------------------------------------
# 360dialog payload parsing
# ---------------------------------------------------------------------------

MEDIA_RESPONSES = {
    "image": "I can see you sent an image! Our agent will take a look when you speak. For now, can you tell me what area you're looking at? 😊",
    "audio": "I noticed you sent a voice note — I can't listen just yet, but our agent will hear it. Meanwhile, can you tell me your budget range?",
    "document": "Got your document! Our agent will review it. Can I ask — what type of property are you looking for?",
    "video": "Thanks for the video! Our agent will check it out. What location are you considering?"
}

def parse_360dialog_payload(payload: dict) -> Optional[IncomingMessage]:
    """
    Parse a 360dialog inbound webhook payload into our IncomingMessage model.
    """
    try:
        messages = payload.get("messages")
        if not messages:
            logger.warning("Received status-update payload — no messages to process.")
            return None

        msg = messages[0]
        msg_type = msg.get("type", "text")
        media_url = None

        if msg_type == "text":
            body = msg["text"]["body"].strip()
        elif msg_type in MEDIA_RESPONSES:
            body = f"[{msg_type.upper()}_MESSAGE]"  # Sentinel for media
            if msg_type in msg:
                media_url = msg[msg_type].get("link") # 360dialog style
        else:
            return None

        # Optional lead source tag: "[source:facebook_ad] Hi there"
        source = "360dialog"
        source_match = re.match(r"^\[source:([a-z0-9_]+)\]\s*", body, re.IGNORECASE)
        if source_match:
            source = source_match.group(1).lower()
            body = body[source_match.end():].strip()

        # 360dialog v1 webhooks don't reliably carry the recipient business number;
        # the gateway falls back to the ?channel=... URL param when this is None.
        external_id = (payload.get("metadata") or {}).get("phone_number_id")

        return IncomingMessage(
            phone_number=msg["from"],
            message=body,
            message_id=msg["id"],
            message_type=msg_type,
            source=source,
            media_url=media_url,
            channel_provider="360dialog",
            channel_external_id=external_id,
        )

    except (KeyError, TypeError, ValueError) as exc:
        logger.error("Failed to parse 360dialog payload: %s | payload=%s", exc, payload)
        return None

def parse_evolution_payload(payload: dict) -> Optional[IncomingMessage]:
    """
    Parse an Evolution API (Baileys) inbound webhook payload.
    Expected event: messages.upsert
    """
    try:
        event = payload.get("event")
        data = payload.get("data")

        # We only process message upserts
        if event != "messages.upsert" or not data:
            logger.debug("Skipping non-message Evolution event: %s", event)
            return None

        # Ignore messages sent by the bot itself
        if data.get("key", {}).get("fromMe"):
            return None

        message_content = data.get("message", {})
        if not message_content:
            return None

        # Extract text from conversation or extendedTextMessage
        body = ""
        if "conversation" in message_content:
            body = message_content["conversation"]
        elif "extendedTextMessage" in message_content:
            body = message_content["extendedTextMessage"].get("text", "")
        
        # Handle media types mapping
        msg_type = "text"
        media_types = ["imageMessage", "audioMessage", "videoMessage", "documentMessage"]
        for mt in media_types:
            if mt in message_content:
                msg_type = mt.replace("Message", "")
                body = f"[{msg_type.upper()}_MESSAGE]"
                break

        if not body:
            return None

        # Clean phone number (remove @s.whatsapp.net)
        remote_jid = data["key"]["remoteJid"]
        phone_number = remote_jid.split("@")[0]

        # The Evolution instance name identifies which connected WhatsApp the
        # message landed on — that's the per-tenant channel key.
        instance = payload.get("instance")

        return IncomingMessage(
            phone_number=phone_number,
            message=body,
            message_id=data["key"]["id"],
            message_type=msg_type,
            source="evolution",
            channel_provider="evolution",
            channel_external_id=instance,
        )

    except (KeyError, TypeError, ValueError) as exc:
        logger.error("Failed to parse Evolution payload: %s | payload=%s", exc, payload)
        return None


def parse_cloud_api_payload(payload: dict) -> Optional[IncomingMessage]:
    """
    Parse a Meta WhatsApp Cloud API inbound webhook payload.
    Expected format is deeply nested, e.g.:
    payload["entry"][0]["changes"][0]["value"]["messages"][0]
    """
    try:
        entries = payload.get("entry", [])
        if not entries:
            return None

        changes = entries[0].get("changes", [])
        if not changes:
            return None

        value = changes[0].get("value", {})

        # We also receive statuses (read receipts), we only want messages
        if "messages" not in value or not value["messages"]:
            return None

        # The business phone number that received the message — the per-tenant
        # channel key for Cloud API (stable across the account's display number).
        external_id = (value.get("metadata") or {}).get("phone_number_id")

        msg = value["messages"][0]
        phone_number = msg.get("from")
        message_id = msg.get("id")
        msg_type = msg.get("type", "text")
        
        body = ""
        media_url = None

        if msg_type == "text":
            body = msg["text"]["body"].strip()
        elif msg_type == "interactive":
            # E.g. button replies or list replies
            interactive = msg.get("interactive", {})
            itype = interactive.get("type")
            if itype == "button_reply":
                body = interactive["button_reply"].get("title", "")
            elif itype == "list_reply":
                body = interactive["list_reply"].get("title", "")
        elif msg_type in MEDIA_RESPONSES:
            body = f"[{msg_type.upper()}_MESSAGE]"
            # Meta doesn't provide media links immediately in webhooks, just media IDs.
            # We would need to query the Graph API to get the download URL.
            media_id = msg[msg_type].get("id")
            if media_id:
                media_url = f"media_id:{media_id}"
        else:
            return None

        if not body or not phone_number:
            return None

        return IncomingMessage(
            phone_number=phone_number,
            message=body,
            message_id=message_id,
            message_type=msg_type,
            source="cloud",
            media_url=media_url,
            channel_provider="cloud",
            channel_external_id=external_id,
        )

    except (KeyError, TypeError, ValueError, IndexError) as exc:
        logger.error("Failed to parse Cloud API payload: %s | payload=%s", exc, payload)
        return None

# ---------------------------------------------------------------------------
# Routes — All traffic flows through the unified gateway
# ---------------------------------------------------------------------------

@router.get(
    "/whatsapp",
    summary="Webhook verification",
    response_class=JSONResponse,
)
async def verify_webhook():
    """
    Some WhatsApp / 360dialog setups issue a GET to verify the endpoint exists.
    """
    return {"status": "Reva webhook active"}


@router.post("/360dialog", response_model=WebhookAckResponse)
async def whatsapp_360dialog_webhook(request: Request):
    """
    Inbound message webhook for 360dialog.
    Parses payload, then delegates to the gateway for signature verification,
    rate limiting, deduplication, and Celery dispatch.
    """
    try:
        payload = await request.json()
    except Exception:
        # Always ack malformed bodies so 360dialog does not retry on our parse errors.
        logger.warning("360dialog webhook: malformed JSON body — acking to stop retries")
        return WebhookAckResponse(status="ignored")
    incoming = parse_360dialog_payload(payload)
    return await gateway.ingest(request, incoming)


@router.post("/evolution", response_model=WebhookAckResponse)
async def whatsapp_evolution_webhook(request: Request):
    """
    Inbound message webhook for Evolution API.
    Parses payload, then delegates to the gateway for security and dispatch.
    """
    try:
        payload = await request.json()
        logger.info(f"== EVOLUTION RAW WEBHOOK == {payload}")
    except Exception:
        logger.warning("Evolution webhook: malformed JSON body — acking to stop retries")
        return WebhookAckResponse(status="ignored")
    incoming = parse_evolution_payload(payload)
    if incoming is None:
        logger.info("Evolution payload parsing returned None (ignored).")
    return await gateway.ingest(request, incoming)

@router.get("/cloud", summary="Meta Cloud API Challenge Validation")
async def verify_cloud_webhook(
    request: Request,
):
    """
    Meta Cloud API sends a GET request to verify the webhook URL.
    It expects the 'hub.challenge' to be echoed back natively if token matches.
    """
    from fastapi import Response
    
    verify_token = os.getenv("WHATSAPP_CLOUD_VERIFY_TOKEN", "")
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == verify_token:
        # Return plain integer challenge without JSON wrapping
        return Response(content=challenge, media_type="text/plain", status_code=200)

    # If verification fails, return generic JSON to avoid hinting
    return {"status": "verification failed"}


@router.post("/cloud", response_model=WebhookAckResponse)
async def whatsapp_cloud_webhook(request: Request):
    """
    Inbound message webhook for Meta WhatsApp Cloud API.
    """
    try:
        payload = await request.json()
    except Exception:
        logger.warning("Cloud API webhook: malformed JSON body")
        return WebhookAckResponse(status="ignored")
        
    incoming = parse_cloud_api_payload(payload)
    return await gateway.ingest(request, incoming)

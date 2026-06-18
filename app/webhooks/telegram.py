import os
from fastapi import APIRouter, Request
from app.core.logger import get_logger
from app.models.lead import IncomingMessage, WebhookAckResponse
from app.webhooks.gateway import gateway

logger = get_logger(__name__)
router = APIRouter(tags=["Telegram Webhook"])

@router.post(
    "/telegram",
    summary="Receive inbound Telegram messages",
    response_model=WebhookAckResponse,
    status_code=200,
)
async def telegram_webhook(
    request: Request
) -> WebhookAckResponse:
    """
    Primary inbound webhook endpoint for Telegram.
    """
    payload = await request.json()
    
    # Simple direct extract for Telegram
    message = payload.get("message")
    if not message:
        return WebhookAckResponse(status="ignored")

    text = message.get("text", "")
    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    message_id = str(message.get("message_id", ""))

    if not text or not chat_id:
        return WebhookAckResponse(status="ignored")

    incoming = IncomingMessage(
        phone_number=chat_id,
        message=text,
        message_id=message_id,
        message_type="text",
        source="telegram"
    )

    return await gateway.ingest(request, incoming)

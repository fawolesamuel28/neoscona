import hmac
import hashlib
import os
import logging
from typing import Optional, Dict, Any

from fastapi import Request, HTTPException
from app.models.lead import IncomingMessage, WebhookAckResponse
from app.cache.redis import is_rate_limited, is_already_received, mark_as_received
from app.workers.tasks import process_message_task
from app.core.logger import get_logger

logger = get_logger(__name__)

class Gateway:
    """
    Unified entry point for all lead ingestion channels.
    Ensures security, reliability, and consistency across providers.
    """

    @staticmethod
    async def verify_signature(request: Request, source: str) -> bool:
        """
        Verify the webhook signature from the provider.
        """
        if source == "telegram":
            # Telegram secret token verification
            secret_token = os.getenv("TELEGRAM_WEBHOOK_SECRET")
            header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if secret_token and header_token != secret_token:
                logger.warning("Telegram signature mismatch")
                return False
            return True

        elif source == "360dialog":
            secret = os.getenv("WHATSAPP_360DIALOG_SECRET")
            if not secret:
                return True
            
            body = await request.body()
            signature = request.headers.get("X-Hub-Signature-256")
            if not signature:
                return False
            
            expected = "sha256=" + hmac.new(
                secret.encode(), body, hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(signature, expected)

        elif source == "evolution":
            secret = os.getenv("EVOLUTION_WEBHOOK_SECRET")
            header_token = request.headers.get("webhook-key")
            if secret and header_token != secret:
                logger.warning("Evolution signature mismatch")
                return False
            return True

        return True

    @classmethod
    async def ingest(cls, request: Request, incoming: Optional[IncomingMessage]) -> WebhookAckResponse:
        """
        The core ingestion pipeline: Verify -> Dedup -> Rate Limit -> Dispatch
        """
        if not incoming:
            return WebhookAckResponse(status="ignored")

        # 1. Signature Verification
        if not await cls.verify_signature(request, incoming.source):
            raise HTTPException(status_code=401, detail="Invalid signature")

        # 2. Duplicate Detection (Gateway Level)
        if await is_already_received(incoming.message_id):
            logger.info("Duplicate message blocked at gateway: %s", incoming.message_id)
            return WebhookAckResponse(status="duplicate")

        # 3. Rate Limiting
        if await is_rate_limited(incoming.phone_number):
            logger.warning("Rate limit hit for %s", incoming.phone_number)
            return WebhookAckResponse(status="rate_limited")

        # 4. Commit and Dispatch
        try:
            # Mark as received immediately to block further webhook retries
            await mark_as_received(incoming.message_id)
            
            process_message_task.delay(
                incoming.phone_number,
                incoming.message,
                incoming.message_id,
                incoming.message_type,
                incoming.source,
                incoming.media_url
            )
            
            logger.info("Message %s accepted for processing from %s", incoming.message_id, incoming.source)
            return WebhookAckResponse(status="accepted")
            
        except Exception as e:
            logger.error("Failed to dispatch message %s: %s", incoming.message_id, e)
            return WebhookAckResponse(status="error")

gateway = Gateway()

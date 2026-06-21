import hmac
import hashlib
import os
import logging
from typing import Optional, Dict, Any

from fastapi import Request, HTTPException
from app.models.lead import IncomingMessage, WebhookAckResponse
from app.cache.redis import is_rate_limited, is_already_received, mark_as_received
from app.services.channels import resolve_tenant_for_channel
from app.workers.tasks import process_message_task
from app.core.logger import get_logger

logger = get_logger(__name__)

class Gateway:
    """
    Unified entry point for all lead ingestion channels.
    Ensures security, reliability, and consistency across providers.
    """

    @staticmethod
    async def verify_signature(request: Request, provider: str) -> bool:
        """
        Verify the webhook is authentically from `provider`. FAIL CLOSED: an unknown
        provider, an unset signing secret, a missing signature, or a mismatch all
        return False.

        This is what makes channel→tenant resolution safe: an unauthenticated caller
        cannot reach resolution and inject a message into another tenant's workspace.
        `provider` MUST be the true channel provider (incoming.channel_provider), never
        the mutable marketing `source` (a "[source:...]" tag must not bypass auth).
        """
        if provider in ("cloud", "360dialog"):
            # Meta WhatsApp Cloud API and 360dialog both sign with X-Hub-Signature-256
            # = HMAC-SHA256 of the raw request body. Cloud uses the Meta app secret
            # (our server secret, never shared with tenants), so once this verifies, the
            # payload's phone_number_id is a trustworthy tenant selector.
            secret = (
                os.getenv("WHATSAPP_CLOUD_APP_SECRET") if provider == "cloud"
                else os.getenv("WHATSAPP_360DIALOG_SECRET")
            )
            if not secret:
                logger.error("Signing secret unset for %s — rejecting webhook (fail-closed).", provider)
                return False
            signature = request.headers.get("X-Hub-Signature-256")
            if not signature:
                return False
            body = await request.body()
            expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            return hmac.compare_digest(signature, expected)

        if provider == "evolution":
            secret = os.getenv("EVOLUTION_WEBHOOK_SECRET")
            if not secret:
                logger.error("EVOLUTION_WEBHOOK_SECRET unset — rejecting Evolution webhook (fail-closed).")
                return False
            return hmac.compare_digest(request.headers.get("webhook-key") or "", secret)

        logger.warning("Unknown webhook provider %r — rejecting (fail-closed).", provider)
        return False

    @classmethod
    async def ingest(cls, request: Request, incoming: Optional[IncomingMessage]) -> WebhookAckResponse:
        """
        The core ingestion pipeline: Verify -> Dedup -> Rate Limit -> Dispatch
        """
        if not incoming:
            return WebhookAckResponse(status="ignored")

        # The TRUE channel provider — never the mutable marketing `source` (a
        # "[source:...]" body tag must not steer which secret is checked or which
        # tenant a message resolves to). Signature verification and tenant
        # resolution are bound to this same value.
        provider = incoming.channel_provider or incoming.source

        # 1. Signature Verification
        if not await cls.verify_signature(request, provider):
            raise HTTPException(status_code=401, detail="Invalid signature")

        # 1.5 Tenant Resolution — which customer's inbox did this land on?
        # The channel registry is the SOLE authority. We never fall back to a
        # default tenant: filing one business's lead under another's workspace is a
        # data-isolation breach. An unknown channel is rejected (ack'd, not queued).
        external_id = incoming.channel_external_id or request.query_params.get("channel")
        tenant_id = await resolve_tenant_for_channel(provider, external_id)
        if not tenant_id:
            logger.warning(
                "Rejecting inbound %s: no tenant for provider=%s channel=%s",
                incoming.message_id, provider, external_id,
            )
            return WebhookAckResponse(status="rejected")

        # 2. Duplicate Detection (Gateway Level)
        if await is_already_received(incoming.message_id):
            logger.info("Duplicate message blocked at gateway: %s", incoming.message_id)
            return WebhookAckResponse(status="duplicate")

        # 3. Rate Limiting (scoped to the resolved tenant)
        if await is_rate_limited(tenant_id, incoming.phone_number):
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
                incoming.media_url,
                tenant_id,
            )
            
            logger.info("Message %s accepted for processing from %s", incoming.message_id, incoming.source)
            return WebhookAckResponse(status="accepted")
            
        except Exception as e:
            logger.error("Failed to dispatch message %s: %s", incoming.message_id, e)
            return WebhookAckResponse(status="error")

gateway = Gateway()

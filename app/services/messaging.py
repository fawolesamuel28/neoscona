import asyncio
import os
import traceback
from typing import Optional

import httpx
from app.core.logger import get_logger
from app.core.circuit_breaker import whatsapp_breaker

logger = get_logger(__name__)


async def send_typing_indicator(phone_number: str, source: str = "whatsapp_organic") -> None:
    """
    Sends a WhatsApp typing/presence indicator.
    """
    provider = os.getenv("WHATSAPP_PROVIDER", "360dialog")
    if provider == "evolution":
        # Evolution automatically handles typing if enabled, or we skip
        pass
    else:
        api_key = os.getenv("WHATSAPP_API_KEY")
        base_url = os.getenv("WHATSAPP_BASE_URL", "https://waba.360dialog.io/v1")
        if not api_key:
            return
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{base_url}/contacts/{phone_number}/presence",
                    json={"presence": "composing"},
                    headers={"D360-API-KEY": api_key}
                )
        except Exception:
            pass


async def send_property_images(phone_number: str, property_data: dict, source: str = "whatsapp_organic") -> None:
    """
    Sends property images directly in WhatsApp chat.
    Lead never has to click a link or open a browser.
    """
    provider = os.getenv("WHATSAPP_PROVIDER", "cloud").lower()
    
    images = property_data.get("images", [])
    if not images:
        return
        
    if provider == "cloud":
        for image_url in images[:3]:
            await whatsapp_breaker.call(
                send_cloud_api_image, 
                phone_number, 
                image_url, 
                f"{property_data['name']} — ₦{property_data['price']:,.0f}"
            )
        return
        
    api_key = os.getenv("WHATSAPP_API_KEY")
    base_url = os.getenv("WHATSAPP_BASE_URL", "https://waba.360dialog.io/v1")
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        for image_url in images[:3]:  # Max 3 images per property
            await client.post(
                f"{base_url}/messages",
                json={
                    "to": phone_number,
                    "type": "image",
                    "image": {
                        "link": image_url,
                        "caption": f"{property_data['name']} — ₦{property_data['price']:,.0f}"
                    }
                },
                headers={"D360-API-KEY": api_key}
            )

async def send_cloud_api_message(phone_number: str, text: str) -> None:
    """Send an outbound message via Meta WhatsApp Cloud API."""
    token = os.getenv("WHATSAPP_CLOUD_TOKEN")
    phone_id = os.getenv("WHATSAPP_CLOUD_PHONE_ID")
    
    if not token or not phone_id:
        logger.info("[DEV CLOUD API] -> %s: %s", phone_number, text)
        return

    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone_number,
        "type": "text",
        "text": {
            "preview_url": True,
            "body": text
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code not in (200, 201):
            logger.error("Cloud API send failed: %s %s", response.status_code, response.text)
    except Exception as exc:
        logger.error("Cloud API network error: %s", exc)


async def send_cloud_api_image(phone_number: str, image_url: str, caption: str) -> None:
    """Send an outbound image via Meta WhatsApp Cloud API."""
    token = os.getenv("WHATSAPP_CLOUD_TOKEN")
    phone_id = os.getenv("WHATSAPP_CLOUD_PHONE_ID")
    
    if not token or not phone_id:
        logger.info("[DEV CLOUD API IMAGE] -> %s: %s", phone_number, image_url)
        return

    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone_number,
        "type": "image",
        "image": {
            "link": image_url,
            "caption": caption
        }
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code not in (200, 201):
            logger.error("Cloud API image send failed: %s %s", response.status_code, response.text)
    except Exception as exc:
        logger.error("Cloud API image network error: %s", exc)

async def send_outbound_message(phone_number: str, text: str, source: str = "whatsapp_organic") -> None:
    """
    Unified outbound messaging interface. Routes to Evolution, 360dialog, or Cloud API.
    WhatsApp providers are protected by the circuit breaker.
    """
    logger.info("Sending outbound message | source=%s | target=%s", source, phone_number)
    # WhatsApp provider — protected by circuit breaker
    provider = os.getenv("WHATSAPP_PROVIDER", "cloud").lower()
    try:
        if provider == "cloud":
            await whatsapp_breaker.call(send_cloud_api_message, phone_number, text)
        elif provider == "evolution":
            await whatsapp_breaker.call(send_evolution_message, phone_number, text)
        else:
            await whatsapp_breaker.call(send_360dialog_message, phone_number, text)
    except RuntimeError as e:
        # Circuit breaker is open — log but don't crash
        logger.error(f"WhatsApp circuit breaker OPEN: {e}")


async def send_evolution_message(phone_number: str, text: str) -> None:
    """Dispatch message via Evolution API REST endpoint with retry logic."""
    base_url = os.getenv("WHATSAPP_EVOLUTION_URL", "http://localhost:8081")
    instance = os.getenv("WHATSAPP_EVOLUTION_INSTANCE", "Reva2")
    api_key = os.getenv("WHATSAPP_EVOLUTION_API_KEY")

    if not api_key:
        logger.info("[DEV EVOLUTION] -> %s: %s", phone_number, text)
        return

    payload = {
        "number": phone_number,
        "text": text,
        "options": {
            "delay": 1200,
            "presence": "composing"
        }
    }

    url = f"{base_url}/message/sendText/{instance}"
    headers = {"apikey": api_key}
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code in (200, 201):
                return

            if (response.status_code in [400, 500]) and "Connection Closed" in response.text:
                logger.warning(
                    "Evolution instance '%s' is disconnected from WhatsApp. Reconnect via UI.",
                    instance,
                )
                return

            logger.error(
                "Evolution send failed (attempt %d/%d): %s %s",
                attempt, max_retries, response.status_code, response.text,
            )

        except Exception as exc:
            logger.error(
                "Evolution network error [%s] (attempt %d/%d): %s",
                type(exc).__name__, attempt, max_retries, exc,
            )

        if attempt < max_retries:
            await asyncio.sleep(2 ** attempt)


async def send_360dialog_message(phone_number: str, text: str) -> None:
    """Legacy 360dialog sender."""
    api_key: Optional[str] = os.getenv("WHATSAPP_API_KEY")
    base_url: str = os.getenv("WHATSAPP_BASE_URL", "https://waba.360dialog.io/v1")

    if not api_key:
        logger.info("[DEV 360] -> %s: %s", phone_number, text)
        return

    payload = {
        "to": phone_number,
        "type": "text",
        "text": {"body": text},
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{base_url}/messages",
                json=payload,
                headers={
                    "D360-API-KEY": api_key,
                    "Content-Type": "application/json",
                },
            )
        if response.status_code not in (200, 201):
            logger.error("360dialog send failed: %s %s", response.status_code, response.text)
    except Exception as exc:
        logger.error("360dialog network error: %s", exc)

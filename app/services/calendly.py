import os
import httpx
import logging

logger = logging.getLogger(__name__)

CALENDLY_API_BASE = "https://api.calendly.com"


async def get_user_uri() -> str | None:
    """
    Fetches your Calendly user URI.
    Needed to scope API calls to your account.
    """
    token = os.getenv("CALENDLY_API_TOKEN")
    if not token:
        logger.warning("CALENDLY_API_TOKEN not set")
        return None

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{CALENDLY_API_BASE}/users/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code == 200:
            return response.json()["resource"]["uri"]
        logger.error(f"Failed to get Calendly user: {response.text}")
        return None


async def get_available_slots(days_ahead: int = 7) -> list:
    """
    Fetches available meeting slots for the next N days.
    Used to tell the lead when agents are free.
    """
    try:
        token = os.getenv("CALENDLY_API_TOKEN")
        user_uri = await get_user_uri()

        if not token or not user_uri:
            return []

        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days_ahead)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{CALENDLY_API_BASE}/event_type_available_times",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "event_type": os.getenv("CALENDLY_EVENT_URL"),
                    "start_time": now.isoformat(),
                    "end_time": end.isoformat()
                }
            )

            if response.status_code == 200:
                slots = response.json().get("collection", [])
                return slots[:5]  # Return next 5 available slots
            return []

    except Exception as e:
        logger.error(f"Failed to fetch available slots: {e}")
        return []


async def create_booking_link(
    phone_number: str,
    lead_name: str | None = None
) -> str:
    """
    Returns a prefilled Calendly booking link for this lead.
    Calendly handles the actual scheduling UI.
    """
    base_url = os.getenv("CALENDLY_EVENT_URL")

    if not base_url:
        logger.warning("CALENDLY_EVENT_URL not set — returning placeholder")
        return "https://calendly.com/your-agent/property-consultation"

    # Prefill lead's name if we have it
    if lead_name:
        return f"{base_url}?name={lead_name.replace(' ', '+')}"

    return base_url


async def verify_booking_made(invitee_uri: str) -> dict | None:
    """
    Called from Calendly webhook when a lead actually books.
    Returns booking details.
    """
    try:
        token = os.getenv("CALENDLY_API_TOKEN")
        async with httpx.AsyncClient() as client:
            response = await client.get(
                invitee_uri,
                headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code == 200:
                return response.json()["resource"]
            return None

    except Exception as e:
        logger.error(f"Failed to verify booking: {e}")
        return None

from fastapi import APIRouter, Request
from app.services.leads import mark_meeting_booked
from app.services.calendly import verify_booking_made
from app.services.messaging import send_outbound_message
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/calendly")
async def calendly_webhook(request: Request):
    """
    Calendly calls this when a lead books a meeting.
    We update their record and send a confirmation on WhatsApp.
    """
    try:
        payload = await request.json()
        event = payload.get("event")

        # We only care about new bookings
        if event != "invitee.created":
            return {"status": "ok"}

        resource = payload.get("payload", {})
        invitee = resource.get("invitee", {})
        event_resource = resource.get("event", {})

        # Extract lead details from Calendly payload
        phone_number = invitee.get("text_reminder_number", "").replace("+", "")
        invitee_name = invitee.get("name", "")
        meeting_url = invitee.get("uri", "")
        start_time = event_resource.get("start_time", "")

        if not phone_number:
            logger.warning("Calendly webhook received without phone number")
            return {"status": "ok"}

        # Update lead in Supabase
        await mark_meeting_booked(phone_number, meeting_url)

        # Send WhatsApp confirmation
        confirmation = (
            f"You're confirmed! ✅\n\n"
            f"Your property consultation is booked.\n"
            f"📅 {start_time}\n\n"
            f"Our consultant will call you at this number. "
            f"Feel free to reach out if you need to reschedule 🙏"
        )

        await send_outbound_message(phone_number, confirmation)
        logger.info(f"Meeting confirmed for {phone_number}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Calendly webhook error: {e}")
        return {"status": "error", "detail": str(e)}

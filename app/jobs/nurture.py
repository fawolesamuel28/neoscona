import asyncio
import random
from datetime import datetime, timezone
import logging

from app.db.supabase import get_supabase
from app.services.messaging import send_outbound_message
from app.core.logger import get_logger

logger = get_logger(__name__)

NURTURE_MESSAGES = {
    3: [
        "Hi! Amara here from Atlantic Horizons 😊 Just checking — still looking around {location}?",
        "Hey! A few new units came in around {location} this week. Your {budget} range could work nicely. Want me to send options?",
    ],
    7: [
        "Hi again — things move fast in Lagos o. Still interested in a {property_type} in {location}?",
        "Quick one — are you still on the hunt? I might have something in {location} that fits 🏠",
    ],
    14: [
        "It's been a bit! No pressure at all — just here if you want to pick this up again 🙏",
        "Hey! Prices in {location} shifted a little. Want an update on what {budget} can get you now?",
    ],
}

def format_nurture_message(template: str, lead: dict) -> str:
    return template.format(
        location=lead.get("location") or "your preferred area",
        budget=lead.get("budget") or "your budget",
        property_type=lead.get("property_type") or "property"
    )

async def run_nurture_job():
    """
    Runs daily. Finds cold leads and sends contextual follow-ups.
    Only nurtures leads in qualifying stage — not new, not done.
    """
    logger.info("Nurture job started")
    db = get_supabase()
    now = datetime.now(timezone.utc)

    def _fetch_cold_leads():
        return db.table("leads")\
            .select("*")\
            .in_("stage", ["qualifying", "qualified"])\
            .eq("meeting_booked", False)\
            .execute()
            
    leads = await asyncio.to_thread(_fetch_cold_leads)

    for lead in leads.data:
        updated_at_str = lead.get("updated_at") or lead.get("created_at")
        if not updated_at_str:
            continue
            
        updated_at = datetime.fromisoformat(updated_at_str.replace('Z', '+00:00'))
        days_cold = (now - updated_at).days

        messages = None
        if days_cold == 3:
            messages = NURTURE_MESSAGES[3]
        elif days_cold == 7:
            messages = NURTURE_MESSAGES[7]
        elif days_cold == 14:
            messages = NURTURE_MESSAGES[14]

        if messages:
            message = format_nurture_message(
                random.choice(messages),
                lead
            )
            await send_outbound_message(lead["phone_number"], message, lead.get("source", "whatsapp_organic"))
            logger.info(f"Nurture sent to {lead['phone_number']} — day {days_cold}")

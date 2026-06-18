from datetime import datetime, timedelta, timezone
import os

from app.core.logger import get_logger
from app.services.leads import mark_meeting_booked

logger = get_logger(__name__)

async def get_next_available_slots(agent_email: str) -> list[dict]:
    """
    Fetches next 3 available 30-min slots from agent's Google Calendar.
    Returns human-readable options to offer inside WhatsApp.
    """
    slots = []
    now = datetime.now(timezone.utc)
    
    # Business hours: 9am–5pm WAT (UTC+1), Mon–Sat
    for day_offset in range(7):
        candidate = now + timedelta(days=day_offset)
        
        if candidate.weekday() == 6:  # Skip Sunday
            continue
        
        for hour in [10, 12, 14, 16]:
            slot_time = candidate.replace(
                hour=hour - 1,  # Convert WAT to UTC
                minute=0,
                second=0,
                microsecond=0
            )
            if slot_time > now:
                slots.append({
                    "datetime": slot_time.isoformat(),
                    "label": slot_time.strftime("%A, %b %d at %I:%M %p")
                })
        
        if len(slots) >= 3:
            break
    
    return slots[:3]


async def present_slots_in_chat(phone_number: str, agent_email: str, source: str = "whatsapp_organic"):
    """
    Instead of just sending a Calendly link,
    Amara offers 3 specific times inside the chat.
    Lead just replies with 1, 2, or 3.
    """
    from app.services.messaging import send_outbound_message
    
    slots = await get_next_available_slots(agent_email)
    
    if not slots:
        return None
    
    message = (
        "For the site visit — I'm free at these times:\n\n"
        f"1️⃣ {slots[0]['label']}\n"
        f"2️⃣ {slots[1]['label']}\n"
        f"3️⃣ {slots[2]['label']}\n\n"
        "Which works for you? Just reply *1*, *2*, or *3* 😊"
    )
    
    await send_outbound_message(phone_number, message, source)
    return slots

async def handle_slot_selection(
    phone_number: str,
    message: str,
    pending_slots: list
) -> str | None:
    
    selection_map = {"1": 0, "2": 1, "3": 2}
    idx = selection_map.get(message.strip())
    
    if idx is None or idx >= len(pending_slots):
        return None
    
    selected = pending_slots[idx]
    
    await mark_meeting_booked(phone_number, selected["label"])
    
    return (
        f"Perfect, you're booked ✅\n\n"
        f"📅 {selected['label']}\n\n"
        f"I'll see you there — save my number. If anything changes, just message me 🏠"
    )

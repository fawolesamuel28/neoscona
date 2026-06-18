"""
Resurrection Job — Weekly manual touch for cold leads.
Finds leads with no activity for 60+ days and sends a re-engagement message.
"""
from datetime import datetime, timedelta, timezone
from app.db.supabase import get_supabase
from app.services.messaging import send_outbound_message
from app.core.logger import get_logger

logger = get_logger(__name__)

async def run_resurrection_job():
    """
    Finds leads that haven't been contacted in 60 days
    and sends them a 'resurrection' message.
    """
    db = get_supabase()
    sixty_days_ago = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    
    # Query for leads that are not booked, not closed, and haven't had activity in 60 days
    response = db.table("leads").select("*").lt("last_contact_at", sixty_days_ago).neq("stage", "closed").neq("meeting_booked", True).execute()
    
    leads = response.data
    logger.info(f"Found {len(leads)} leads for resurrection.")
    
    for lead in leads:
        phone_number = lead["phone_number"]
        source = lead.get("source", "whatsapp_organic")
        
        # A simple resurrection message
        message = "Hi! It's been a while. We have some amazing new property opportunities in Lagos. Would you like to see our latest inventory?"
        
        try:
            await send_outbound_message(phone_number, message, source)
            # Update last_contact_at
            db.table("leads").update({"last_contact_at": datetime.now(timezone.utc).isoformat()}).eq("phone_number", phone_number).execute()
        except Exception as e:
            logger.error(f"Failed to resurrect lead {phone_number}: {e}")

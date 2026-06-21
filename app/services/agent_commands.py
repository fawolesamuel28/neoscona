import re
from typing import Optional, Tuple
from app.services.leads import upsert_lead, get_lead
from app.core.state_machine import LeadStage
from app.services.messaging import send_outbound_message
from app.core.logger import get_logger

logger = get_logger(__name__)

async def handle_agent_command(agent_phone: str, message: str, *, tenant_id: str) -> bool:
    """
    Parses and executes commands sent by agents.
    Returns True if a command was handled, False otherwise.

    `tenant_id` (the channel's tenant — the agent texts on the business's own line)
    scopes every lead write so a command can't touch another workspace's lead.
    """
    msg = message.strip().upper()
    
    # 1. CLOSED Command: CLOSED <phone> <amount>
    closed_match = re.match(r"^CLOSED\s+(\d+)\s+(\d+)$", msg)
    if closed_match:
        lead_phone = closed_match.group(1)
        amount = int(closed_match.group(2))
        
        await upsert_lead(
            lead_phone,
            {"closing_revenue": amount, "attribution": "reva"},
            LeadStage.CLOSED.value,
            tenant_id=tenant_id,
        )
        
        await send_outbound_message(
            agent_phone, 
            f"✅ Deal logged! Lead {lead_phone} marked as CLOSED with ₦{amount:,.0f} revenue.",
            "whatsapp_organic" # Defaulting to whatsapp for agent notifications
        )
        return True

    # 2. PAUSE Command: PAUSE <phone>
    pause_match = re.match(r"^PAUSE\s+(\d+)$", msg)
    if pause_match:
        lead_phone = pause_match.group(1)
        
        await upsert_lead(
            lead_phone,
            {"is_paused": True},
            None,  # Keep current stage
            tenant_id=tenant_id,
        )
        
        await send_outbound_message(
            agent_phone, 
            f"⏸️ Reva PAUSED for lead {lead_phone}. You can now take over manually.",
            "whatsapp_organic"
        )
        return True

    # 3. RESUME Command: RESUME <phone>
    resume_match = re.match(r"^RESUME\s+(\d+)$", msg)
    if resume_match:
        lead_phone = resume_match.group(1)
        
        await upsert_lead(
            lead_phone,
            {"is_paused": False},
            None,
            tenant_id=tenant_id,
        )
        
        await send_outbound_message(
            agent_phone, 
            f"▶️ Reva RESUMED for lead {lead_phone}. Automatic responses active.",
            "whatsapp_organic"
        )
        return True

    return False

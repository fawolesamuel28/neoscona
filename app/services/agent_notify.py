import asyncio
from app.db.supabase import get_supabase
from app.core.logger import get_logger

logger = get_logger(__name__)

async def notify_agent(lead: dict, agent_data: dict):
    """
    Sends detailed lead briefing to the assigned agent on WhatsApp.
    """
    from app.services.messaging import send_outbound_message
    
    agent_whatsapp = agent_data.get("phone")
    if not agent_whatsapp:
        return

    summary = (
        f"🔔 *New Agent Briefing* — {lead.get('name', 'Prospect')}\n\n"
        f"📍 *Interest:* {lead.get('location', 'Lagos')} | {lead.get('property_type', 'N/A')}\n"
        f"💰 *Budget:* {lead.get('budget', 'N/A')}\n"
        f"🔥 *Score:* {lead.get('seriousness_score', '?')}/10\n"
        f"📱 *Phone:* wa.me/{lead.get('phone_number')}\n\n"
        f"📝 *AI Summary:* Lead is interested in {lead.get('intent', 'buying')} and has a {lead.get('urgency', 'medium')} urgency.\n\n"
        f"Reva is currently handling outreach. Type *TAKEOVER {lead.get('phone_number')}* to silence Reva and chat manually."
    )
    await send_outbound_message(agent_whatsapp, summary)


async def route_lead_by_score(lead: dict, score: int):
    """
    High score leads (8–10) should skip Amara entirely after qualification
    and go straight to a senior agent's personal WhatsApp.
    """
    if score >= 8:
        # Hot lead — direct to senior agent immediately
        await notify_agent(lead, "senior_agent@reva.ai")
        return "hot"
    elif score >= 5:
        # Warm lead — normal Calendly flow
        return "warm"
    else:
        # Cold lead — nurture sequence
        return "cold"


async def assign_agent(development_id: str | None = None) -> dict | None:
    """
    Picks the next available agent, scoped by development if provided.
    Fallback to round-robin among all active agents.
    """
    db = get_supabase()
    
    # 1. Fetch agents for this development
    query = db.table("agents").select("*").eq("active", True)
    if development_id:
        # Assuming an intermediate 'agent_developments' table or agent has 'development_id'
        # For this spec, we'll check if the agent has a matching development_id field
        query = query.eq("development_id", development_id)
    
    def _fetch_agents():
        return query.execute()
        
    agents_res = await asyncio.to_thread(_fetch_agents)
    
    # Fallback to all agents if development-specific agents not found
    if not agents_res.data and development_id:
        logger.info(f"No agents found for development {development_id}. Falling back to global pool.")
        def _fetch_all():
            return db.table("agents").select("*").eq("active", True).execute()
        agents_res = await asyncio.to_thread(_fetch_all)

    if not agents_res.data:
        logger.warning("No active agents found in DB for assignment!")
        return None
    
    # 2. Least-loaded selection
    counts = {}
    for agent in agents_res.data:
        def _get_count(agent_id=agent["id"]):
            return db.table("leads")\
                .select("id", count="exact")\
                .eq("assigned_agent_id", agent_id)\
                .execute()
        
        result = await asyncio.to_thread(_get_count)
        counts[agent["id"]] = result.count or 0
    
    least_loaded = min(agents_res.data, key=lambda a: counts[a["id"]])
    logger.info("Assigned lead to agent: %s (%s)", least_loaded.get("name"), least_loaded.get("role", "standard"))
    
    return least_loaded

"""
Portal Sync Job — Polls Gmail for leads from PropertyPro and NPC.
"""
from app.core.logger import get_logger
from app.services.portal_intercept import portal_interceptor

logger = get_logger(__name__)

async def sync_portal_leads():
    """
    Triggers the portal interceptor to check for new emails.
    """
    logger.info("Starting portal sync job...")
    try:
        # Assuming portal_interceptor.check_for_leads() is the main entry point
        # We'll use a placeholder if the implementation differs
        await portal_interceptor.check_for_leads()
    except Exception as e:
        logger.error(f"Portal sync failed: {e}")

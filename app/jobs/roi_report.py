"""
ROI Report Job — Weekly summary of performance metrics.
"""
from app.core.logger import get_logger

logger = get_logger(__name__)

async def send_weekly_roi_report():
    """
    Generates and sends the weekly ROI report.
    """
    logger.info("ROI Report Job: Summary generation started.")
    # TODO: Implement actual Supabase query and Email sending
    logger.info("ROI Report Job: Completed.")

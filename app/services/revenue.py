import csv
import io
from typing import List, Dict
from app.db.supabase import get_supabase
from app.core.logger import get_logger

logger = get_logger(__name__)

async def get_revenue_metrics(tenant_id: str) -> Dict:
    """
    Fetches real-time revenue intelligence for a specific tenant.
    """
    try:
        db = get_supabase()
        result = db.table("revenue_intelligence")\
            .select("*")\
            .eq("tenant_id", tenant_id)\
            .single()\
            .execute()
        return result.data
    except Exception as e:
        logger.error(f"Failed to fetch revenue metrics: {e}")
        return {}

async def export_leads_csv(tenant_id: str) -> str:
    """
    Generates a CSV export of all leads for a tenant.
    Returns the CSV data as a string.
    """
    try:
        db = get_supabase()
        result = db.table("leads")\
            .select("*")\
            .eq("tenant_id", tenant_id)\
            .order("created_at", desc=True)\
            .execute()
        
        leads = result.data
        if not leads:
            return ""

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=leads[0].keys())
        writer.writeheader()
        writer.writerows(leads)
        
        return output.getvalue()
    except Exception as e:
        logger.error(f"CSV Export failed: {e}")
        return ""

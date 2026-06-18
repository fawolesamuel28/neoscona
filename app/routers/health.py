from fastapi import APIRouter
from datetime import datetime, timezone
from app.cache.redis import get_redis_client
from app.db.supabase import get_supabase
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["Reliability"])

@router.get("/health")
async def deep_health_check():
    """
    Comprehensive health check of all vital systems.
    Used by monitoring services (UptimeRobot, etc.)
    """
    checks = {}
    
    # 1. Redis Check
    try:
        client = await get_redis_client()
        await client.ping()
        checks["redis"] = "ok"
    except Exception as e:
        logger.error(f"Health Check: Redis down: {e}")
        checks["redis"] = "down"
    
    # 2. Supabase Check
    try:
        db = get_supabase()
        db.table("leads").select("id").limit(1).execute()
        checks["database"] = "ok"
    except Exception as e:
        logger.error(f"Health Check: Database down: {e}")
        checks["database"] = "down"
    
    # 3. LLM Circuit Breaker Check (Redis-backed)
    try:
        client = await get_redis_client()
        cb_state = await client.hget("circuit_breaker:llm", "state")
        checks["llm"] = "ok" if cb_state in (None, b"closed", "closed") else "degraded"
    except Exception:
        checks["llm"] = "unknown"

    # 4. WhatsApp Circuit Breaker Check
    try:
        client = await get_redis_client()
        wa_state = await client.hget("circuit_breaker:whatsapp", "state")
        checks["whatsapp"] = "ok" if wa_state in (None, b"closed", "closed") else "degraded"
    except Exception:
        checks["whatsapp"] = "unknown"
    
    overall = "healthy" if all(v == "ok" for v in checks.values()) else "degraded"
    
    return {
        "status": overall,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0-enterprise"
    }

"""
Dashboard real-time events via Redis pub/sub.

Celery workers publish; the FastAPI process subscribes and fans out to WebSockets.
"""

from __future__ import annotations

import json
import os
from typing import Any

from app.core.logger import get_logger

logger = get_logger(__name__)

CHANNEL = "reva:dashboard:events"

# Event types (JSON payloads):
#   ping
#   pipeline_updated  — refresh leads + stats (+ optional phone_number for detail)
#   voice_updated     — refresh voice list + stats (+ optional voice_lead_id)


def notify_dashboard_update(
    event_type: str = "pipeline_updated",
    *,
    phone_number: str | None = None,
    voice_lead_id: int | None = None,
) -> None:
    """Publish a dashboard event (safe from sync/async, web or Celery)."""
    payload: dict[str, Any] = {"type": event_type}
    if phone_number:
        payload["phone_number"] = phone_number
    if voice_lead_id is not None:
        payload["voice_lead_id"] = voice_lead_id

    try:
        import redis

        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        client = redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        client.publish(CHANNEL, json.dumps(payload, ensure_ascii=False))
        client.close()
    except Exception as exc:
        logger.debug("Dashboard notify skipped: %s", exc)

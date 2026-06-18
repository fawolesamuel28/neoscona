from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth import get_ws_principal
from app.core.dashboard_ws import dashboard_ws_manager

router = APIRouter(tags=["Dashboard WebSocket"])


@router.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket) -> None:
    """
    Real-time dashboard updates. Clients receive JSON events:
    pipeline_updated, voice_updated, ping.

    Auth: pass the Supabase access token as `?token=<jwt>` (and optional
    `&org_id=`) on connect. Unauthenticated sockets are rejected with 4401.
    """
    principal = await get_ws_principal(websocket)
    if principal is None:
        await websocket.close(code=4401)
        return

    await dashboard_ws_manager.connect(websocket)
    try:
        while True:
            # Drain client messages (pong / future auth); ignore payload
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await dashboard_ws_manager.disconnect(websocket)

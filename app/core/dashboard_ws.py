"""
WebSocket connection manager + Redis listener for dashboard clients.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.core.dashboard_events import CHANNEL
from app.core.logger import get_logger

logger = get_logger(__name__)


class DashboardWSManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._listener_task: asyncio.Task | None = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info("Dashboard WS connected (%s clients)", len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.info("Dashboard WS disconnected (%s clients)", len(self._connections))

    async def broadcast(self, message: str) -> None:
        async with self._lock:
            targets = list(self._connections)
        if not targets:
            return

        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    async def start_redis_listener(self) -> None:
        if self._listener_task and not self._listener_task.done():
            return
        self._listener_task = asyncio.create_task(self._redis_listen_loop())

    async def stop_redis_listener(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

    async def _redis_listen_loop(self) -> None:
        import redis.asyncio as aioredis

        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        backoff = 1.0

        while True:
            pubsub = None
            client = None
            try:
                client = aioredis.from_url(url, decode_responses=True)
                pubsub = client.pubsub()
                await pubsub.subscribe(CHANNEL)
                logger.info("Dashboard Redis listener subscribed → %s", CHANNEL)
                backoff = 1.0

                async for raw in pubsub.listen():
                    if raw.get("type") != "message":
                        continue
                    data = raw.get("data")
                    if isinstance(data, str):
                        await self.broadcast(data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Dashboard Redis listener error: %s (retry in %ss)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                if pubsub:
                    try:
                        await pubsub.unsubscribe(CHANNEL)
                        await pubsub.aclose()
                    except Exception:
                        pass
                if client:
                    try:
                        await client.aclose()
                    except Exception:
                        pass

    async def run_ping_loop(self) -> None:
        """Keep connections alive through proxies (Railway, etc.)."""
        while True:
            await asyncio.sleep(25)
            await self.broadcast(json.dumps({"type": "ping"}))


dashboard_ws_manager = DashboardWSManager()

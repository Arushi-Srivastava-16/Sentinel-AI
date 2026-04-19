"""
WebSocket ConnectionManager.

Manages all active dashboard WebSocket connections.
Receives events from Redis Pub/Sub and broadcasts to all connected clients.

Pattern:
  Redis Pub/Sub (sentinel:dashboard:events)
       ↓
  ConnectionManager._listen_redis()   [background asyncio task]
       ↓
  ConnectionManager.broadcast()
       ↓
  All active WebSocket connections
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog
from fastapi import WebSocket

from gateway.config import settings

log = structlog.get_logger()

# How often to send a ping to detect dead connections (seconds)
_HEARTBEAT_INTERVAL = 30
# Disconnect if client misses this many pings
_MAX_MISSED_PINGS = 2


class ConnectionManager:
    def __init__(self) -> None:
        # session_id → WebSocket
        self._connections: dict[str, WebSocket] = {}
        # Track missed heartbeats per connection
        self._missed_pings: dict[str, int] = {}
        self._redis_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

    async def startup(self) -> None:
        """Call on app startup — starts background Redis listener and heartbeat."""
        self._redis_task = asyncio.create_task(self._listen_redis())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        log.info("ws_manager_started")

    async def shutdown(self) -> None:
        """Call on app shutdown."""
        if self._redis_task:
            self._redis_task.cancel()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        for ws in list(self._connections.values()):
            await ws.close()
        self._connections.clear()
        log.info("ws_manager_stopped")

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[session_id] = websocket
        self._missed_pings[session_id] = 0
        log.info("ws_connected", session_id=session_id, total=len(self._connections))

        # Send initial connection ack
        await websocket.send_text(json.dumps({
            "event_type": "connected",
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))

    def disconnect(self, session_id: str) -> None:
        self._connections.pop(session_id, None)
        self._missed_pings.pop(session_id, None)
        log.info("ws_disconnected", session_id=session_id, total=len(self._connections))

    async def broadcast(self, message: str) -> None:
        """Send a message to all connected clients. Remove dead connections."""
        dead: list[str] = []
        for session_id, ws in list(self._connections.items()):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(session_id)

        for sid in dead:
            self.disconnect(sid)

    async def send_to(self, session_id: str, message: str) -> None:
        """Send to a specific session."""
        ws = self._connections.get(session_id)
        if ws:
            try:
                await ws.send_text(message)
            except Exception:
                self.disconnect(session_id)

    async def handle_client_message(self, session_id: str, data: str) -> None:
        """Handle messages from dashboard client (e.g. pong responses)."""
        try:
            msg = json.loads(data)
            if msg.get("type") == "pong":
                self._missed_pings[session_id] = 0
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def _listen_redis(self) -> None:
        """Subscribe to Redis Pub/Sub and broadcast events to all clients."""
        from shared.redis_client import websocket_client
        redis = websocket_client()
        try:
            pubsub = redis.pubsub()
            await pubsub.subscribe(settings.ws_pubsub_channel)
            log.info("ws_redis_subscribed", channel=settings.ws_pubsub_channel)

            async for message in pubsub.listen():
                if message["type"] == "message":
                    await self.broadcast(message["data"])
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("ws_redis_listener_error", error=str(e))
        finally:
            try:
                await redis.aclose()
            except Exception:
                pass

    async def _heartbeat_loop(self) -> None:
        """Send pings every 30s. Disconnect clients that miss 2 pings."""
        while True:
            try:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                ping_msg = json.dumps({"type": "ping", "timestamp": datetime.now(timezone.utc).isoformat()})
                dead: list[str] = []

                for session_id in list(self._connections.keys()):
                    missed = self._missed_pings.get(session_id, 0)
                    if missed >= _MAX_MISSED_PINGS:
                        log.info("ws_client_timeout", session_id=session_id)
                        dead.append(session_id)
                    else:
                        self._missed_pings[session_id] = missed + 1
                        await self.send_to(session_id, ping_msg)

                for sid in dead:
                    ws = self._connections.get(sid)
                    if ws:
                        try:
                            await ws.close()
                        except Exception:
                            pass
                    self.disconnect(sid)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("ws_heartbeat_error", error=str(e))


# Module-level singleton — used by the route and by startup/shutdown hooks
manager = ConnectionManager()

"""
WebSocket endpoint — GET /ws/dashboard

Auth: short-lived JWT in ?token= query param.
For Phase 1 demo compatibility, accepts the agent API key directly as well.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from gateway.auth.api_keys import validate_api_key
from gateway.metrics import websocket_connections_active
from gateway.websocket.manager import manager
from shared.redis_client import rate_limit_client

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/dashboard")
async def dashboard_websocket(
    websocket: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    """
    Real-time event stream for the dashboard.

    Auth: pass API key or JWT as ?token=<key>
    Events: tool_call_decision, rate_limit_hit, circuit_breaker_state_change,
            policy_activated, agent_registered, ping
    """
    # Validate the token
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    redis = rate_limit_client()
    try:
        meta = await validate_api_key(redis, token)
    finally:
        await redis.aclose()

    if meta is None:
        await websocket.close(code=4003, reason="Invalid or expired token")
        return

    session_id = f"dash_{uuid.uuid4().hex[:8]}"
    await manager.connect(session_id, websocket)
    websocket_connections_active.inc()

    try:
        while True:
            data = await websocket.receive_text()
            await manager.handle_client_message(session_id, data)
    except WebSocketDisconnect:
        manager.disconnect(session_id)
        websocket_connections_active.dec()
    except Exception:
        manager.disconnect(session_id)
        websocket_connections_active.dec()

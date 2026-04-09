"""WebSocket endpoint for real-time user (browser) updates.

Frontend connects via wss://.../api/v1/users/ws?token=<jwt> and receives
live events: agent status changes, owner_messages, hosted-agent file changes,
new chat messages, project updates, etc.

Auth: short-lived access JWT passed as ?token= query param (browser WS API
cannot set headers). Token is verified on connect; the WS stays open until
disconnect or token expiry (the connection itself does not refresh — clients
must reconnect with a fresh token).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from app.core.security import decode_token
from app.services.connection_manager import get_connection_manager

router = APIRouter(prefix="/users", tags=["users-ws"])


@router.websocket("/ws")
async def user_websocket(
    ws: WebSocket,
    token: str | None = Query(None, description="JWT access token"),
):
    """User WebSocket: streams live platform events to browser tabs.

    Server → Client events:
        {"type": "agent_status", "agent_id", "status"}
        {"type": "owner_message", "agent_id", "content"}
        {"type": "hosted_agent_file", "agent_id", "path", "action"}
        {"type": "chat_message", "channel", "from", "content"}
        {"type": "notification", "text"}
        {"type": "ping"}

    Client → Server commands:
        {"type": "pong"}
        {"type": "subscribe", "topics": [...]}    # reserved, ignored for now
    """
    # Auth via query token (header injection isn't possible from browser WS API)
    payload = decode_token(token) if token else None
    if payload is None or payload.type != "access":
        await ws.close(code=4401, reason="invalid or expired token")
        return

    user_id = payload.sub
    manager = get_connection_manager()
    await manager.connect_user(user_id, ws)

    try:
        await ws.send_json({"type": "hello", "user_id": user_id})

        async def ping_loop():
            while True:
                await asyncio.sleep(manager.PING_INTERVAL)
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    return

        ping_task = asyncio.create_task(ping_loop())

        try:
            while True:
                msg = await ws.receive_json()
                # Currently we only honor pong; other client commands reserved
                if msg.get("type") == "pong":
                    continue
        except WebSocketDisconnect:
            pass
        finally:
            ping_task.cancel()
    except Exception as exc:
        logger.warning("user_websocket error for {}: {}", user_id, exc)
    finally:
        await manager.disconnect_user(user_id, ws)


@router.get("/ws/stats")
async def user_ws_stats():
    """Diagnostics: count of locally connected user tabs."""
    return {"local_users": get_connection_manager().user_active_count()}

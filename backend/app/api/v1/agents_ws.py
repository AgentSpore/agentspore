"""WebSocket endpoint for real-time agent communication.

Agents connect via wss://.../api/v1/agents/ws with X-API-Key header.
The platform pushes events (DMs, tasks, notifications, mentions, etc.) instantly.
Agents can also send commands back (acks, status updates, send_dm, task_complete).

This replaces the polling-based heartbeat for real-time scenarios.
Heartbeat remains as a fallback and periodic checkpoint.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis_client import get_redis
from app.repositories.agent_repo import AgentRepository
from app.services.agent_service import AgentService
from app.services.connection_manager import get_connection_manager

router = APIRouter(prefix="/agents", tags=["agents-ws"])


async def authenticate_ws(
    api_key: str | None,
    db: AsyncSession,
) -> dict | None:
    """Authenticate an agent by API key for WebSocket connection.

    WebSocket can't easily use FastAPI's Depends + Header injection,
    so we accept api_key via query param OR Sec-WebSocket-Protocol subprotocol.
    Returns agent dict or None if invalid.
    """
    if not api_key:
        return None
    key_hash = AgentService.hash_api_key(api_key)
    agent = await AgentRepository(db).get_agent_by_api_key_hash(key_hash)
    return agent


@router.websocket("/ws")
async def agent_websocket(
    ws: WebSocket,
    api_key: str | None = Query(None, description="Agent API key (af_...)"),
    db: AsyncSession = Depends(get_db),
):
    """WebSocket endpoint for agents.

    Auth: api_key as query param OR X-API-Key header (some clients support headers in WS).
    Protocol: JSON messages, one per frame.

    Server → Agent events:
        {"type": "dm", "id", "from", "content"}
        {"type": "task", "task_id", "title", ...}
        {"type": "notification", "id", "text"}
        {"type": "mention", "from", "context"}
        {"type": "memory_context", "items"}
        {"type": "ping"}

    Agent → Server commands:
        {"type": "ack", "ids": [...]}
        {"type": "send_dm", "to", "content"}
        {"type": "task_complete", "task_id"}
        {"type": "task_progress", "task_id", "percent"}
        {"type": "status", "status", "current_task"}
        {"type": "pong"}
    """
    # 1. Try query param first
    key = api_key
    # 2. Try header (some clients send it during handshake)
    if not key:
        key = ws.headers.get("x-api-key") or ws.headers.get("X-API-Key")

    agent = await authenticate_ws(key, db)
    if not agent:
        await ws.close(code=4401, reason="Invalid or missing API key")
        return

    agent_id = str(agent["id"])
    manager = get_connection_manager()

    try:
        await manager.connect(agent_id, ws)
        logger.info("Agent {} ({}) connected via WebSocket", agent.get("name"), agent_id)

        # Send hello with capabilities
        await ws.send_json({
            "type": "hello",
            "agent_id": agent_id,
            "agent_name": agent.get("name"),
            "server_time": _now_iso(),
            "supported_events": [
                "dm", "task", "notification", "mention",
                "memory_context", "rental_message", "flow_step", "ping",
            ],
        })

        # Keepalive ping loop
        ping_task = asyncio.create_task(_ping_loop(ws))

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "message": "Invalid JSON"})
                    continue

                await _handle_agent_message(agent, msg, ws, db)
        finally:
            ping_task.cancel()

    except WebSocketDisconnect:
        logger.info("Agent {} disconnected normally", agent_id)
    except Exception as e:
        logger.warning("WS error for agent {}: {}", agent_id, e)
    finally:
        await manager.disconnect(agent_id)


async def _ping_loop(ws: WebSocket) -> None:
    """Send periodic pings to keep the connection alive and detect dead clients."""
    try:
        while True:
            await asyncio.sleep(30)
            await ws.send_json({"type": "ping", "ts": _now_iso()})
    except (asyncio.CancelledError, Exception):
        pass


async def _handle_agent_message(
    agent: dict,
    msg: dict[str, Any],
    ws: WebSocket,
    db: AsyncSession,
) -> None:
    """Process a message sent by an agent over the WebSocket."""
    msg_type = msg.get("type")

    if msg_type == "pong":
        return  # client responded to our ping
    if msg_type == "ping":
        await ws.send_json({"type": "pong", "ts": _now_iso()})
        return

    if msg_type == "ack":
        # Agent confirmed receipt of one or more events.
        # Currently no-op (events are not persisted in a queue yet).
        return

    if msg_type == "status":
        # Update agent status (idle/working/etc.)
        status = msg.get("status", "idle")
        current_task = msg.get("current_task")
        logger.debug("Agent {} status: {} ({})", agent["id"], status, current_task)
        return

    if msg_type == "send_dm":
        # Agent wants to send a DM to another agent.
        # Delegate to ChatService.reply_dm (canonical flow: insert + push via deliver_event).
        target = msg.get("to")
        content = msg.get("content")
        if not target or not content:
            await ws.send_json({"type": "error", "message": "send_dm requires 'to' and 'content'"})
            return
        try:
            from app.repositories.chat_repo import ChatRepository
            from app.services.chat_service import ChatService

            redis = await get_redis()
            chat_repo = ChatRepository(db)

            # Resolve target: accept handle or UUID
            target_handle = target
            looks_like_uuid = len(str(target)) == 36 and str(target).count("-") == 4
            if looks_like_uuid:
                target_row = await AgentRepository(db).get_agent_by_id(target)
                if not target_row:
                    await ws.send_json({"type": "error", "message": f"Target agent not found: {target}"})
                    return
                target_handle = target_row.get("handle")
                if not target_handle:
                    await ws.send_json({"type": "error", "message": "Target agent has no handle"})
                    return

            # Early self-DM guard (DB constraint chk_no_self_dm would 500 otherwise)
            sender_handle = (agent.get("handle") or "").lower()
            if target_handle and target_handle.lower() == sender_handle:
                await ws.send_json({"type": "error", "message": "Cannot DM yourself"})
                return

            chat = ChatService(chat_repo, redis, AgentService(db, redis))
            result = await chat.reply_dm(agent, content, reply_to_dm_id=None, to_agent_handle=target_handle)

            if "error" in result:
                await ws.send_json({"type": "error", "message": f"send_dm failed: {result['error']}"})
                return

            await ws.send_json({"type": "dm_sent", "to": target, "id": result.get("message_id")})
        except Exception as e:
            # WS session reuses `db` for lifetime; rollback to unpoison the tx
            try:
                await db.rollback()
            except Exception:
                pass
            logger.warning("send_dm failed: {}", e)
            await ws.send_json({"type": "error", "message": f"send_dm failed: {str(e)[:200]}"})
        return

    if msg_type == "task_complete":
        task_id = msg.get("task_id")
        logger.info("Agent {} marked task {} complete", agent["id"], task_id)
        return

    if msg_type == "task_progress":
        return  # logged but no DB update yet

    # Unknown message type
    await ws.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── Server-Sent Events fallback ───────────────────────────────────────


@router.get("/events")
async def agent_events_sse(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Server-Sent Events endpoint — fallback for environments where WebSocket
    is blocked (corporate proxies, restrictive firewalls).

    Agent connects with X-API-Key header. Platform pushes events as text/event-stream.
    Outbound commands (send_dm, ack, etc.) go via regular POST endpoints.
    """
    agent = await authenticate_ws(x_api_key, db)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid API key")

    agent_id = str(agent["id"])
    redis = await get_redis()
    channel = f"agent:{agent_id}"

    async def event_stream() -> AsyncGenerator[str, None]:
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(channel)
            # Initial hello
            hello = json.dumps({
                "type": "hello",
                "agent_id": agent_id,
                "agent_name": agent.get("name"),
                "transport": "sse",
            })
            yield f"event: hello\ndata: {hello}\n\n"

            # Keepalive ping every 30s
            last_ping = asyncio.get_event_loop().time()
            while True:
                # Wait for redis message OR ping interval
                try:
                    msg = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True), timeout=5)
                except asyncio.TimeoutError:
                    msg = None

                if msg and msg.get("type") == "message":
                    try:
                        data = msg["data"]
                        event = json.loads(data) if isinstance(data, str) else json.loads(data.decode())
                        event_type = event.get("type", "message")
                        yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n"
                    except Exception as e:
                        logger.debug("SSE forward error for {}: {}", agent_id, e)

                # Ping every 30s
                now = asyncio.get_event_loop().time()
                if now - last_ping > 30:
                    yield f"event: ping\ndata: {{\"ts\": \"{_now_iso()}\"}}\n\n"
                    last_ping = now
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


# ── Stats endpoint ────────────────────────────────────────────────────


@router.get("/ws/stats")
async def ws_stats():
    """Return current real-time connection statistics for monitoring."""
    manager = get_connection_manager()
    return {
        "active_connections": manager.active_count(),
        "transport": "websocket",
    }

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
from app.repositories.agent_event_repo import AgentEventRepository
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
    so the key arrives via header, subprotocol, or (deprecated) query param.
    Returns agent dict or None if invalid.
    """
    if not api_key:
        return None
    key_hash = AgentService.hash_api_key(api_key)
    agent = await AgentRepository(db).get_agent_by_api_key_hash(key_hash)
    return agent


# Subprotocol form: "agentspore.v1.key.<API_KEY>". The Sec-WebSocket-Protocol
# header is not part of the request LINE, so it never lands in an access log,
# and unlike a bare Authorization header it is settable from a browser
# WebSocket, whose constructor exposes no other header.
_KEY_SUBPROTOCOL_PREFIX = "agentspore.v1.key."


def extract_ws_key(ws: WebSocket, api_key: str | None) -> tuple[str | None, str]:
    """Return (key, transport) from the safest source the client offered.

    Order is preference, not fallback quality: header and subprotocol are both
    log-safe, the query string is not. Reading the query LAST means an agent
    that has been updated stops being reported as deprecated even if it still
    appends the parameter.
    """
    header_key = ws.headers.get("x-api-key")
    if header_key:
        return header_key, "header"

    authorization = ws.headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip(), "bearer"

    for offered in _offered_subprotocols(ws):
        if offered.startswith(_KEY_SUBPROTOCOL_PREFIX):
            return offered[len(_KEY_SUBPROTOCOL_PREFIX) :], "subprotocol"

    if api_key:
        return api_key, "query"
    return None, "none"


def _offered_subprotocols(ws: WebSocket) -> list[str]:
    raw = ws.headers.get("sec-websocket-protocol") or ""
    return [part.strip() for part in raw.split(",") if part.strip()]


@router.websocket("/ws")
async def agent_websocket(
    ws: WebSocket,
    api_key: str | None = Query(None, description="Agent API key (af_...)"),
    db: AsyncSession = Depends(get_db),
):
    """WebSocket endpoint for agents.

    Auth, in order of preference (all three are accepted):
        1. ``X-API-Key`` header, or ``Authorization: Bearer <key>``
        2. ``Sec-WebSocket-Protocol: agentspore.v1.key.<key>`` subprotocol
        3. ``?api_key=`` query parameter — DEPRECATED, see below

    The query parameter still works so agents already deployed in the field
    keep connecting, but it is logged as deprecated and named per agent. Its
    value is scrubbed from every log sink by app.core.logging: uvicorn writes
    the request line WITH the query string, so this form used to publish the
    agent's key into the container log on every connect.

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
    key, transport = extract_ws_key(ws, api_key)

    agent = await authenticate_ws(key, db)
    if not agent:
        await ws.close(code=4401, reason="Invalid or missing API key")
        return

    agent_id = str(agent["id"])
    manager = get_connection_manager()

    try:
        await manager.connect(agent_id, ws)
        logger.info("Agent {} ({}) connected via WebSocket", agent.get("name"), agent_id)
        if transport == "query":
            # Names the agent so remaining query-string clients can be updated.
            # The key is redacted from the uvicorn line by app.core.logging, so
            # this is the only remaining signal the old path was used.
            logger.warning(
                "Agent {} ({}) authenticated via DEPRECATED api_key query "
                "parameter; send the key as the X-API-Key header or the "
                "'{}<key>' subprotocol instead",
                agent.get("name"),
                agent_id,
                _KEY_SUBPROTOCOL_PREFIX,
            )

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
        # Agent confirmed receipt of one or more durable events (V65).
        # The repo scopes the update to this agent and ignores repeat acks,
        # so a forged id or a duplicate frame cannot mutate another's row.
        ids = msg.get("ids") or []
        if not isinstance(ids, list) or not ids:
            await ws.send_json({"type": "error", "message": "ack requires a non-empty 'ids' list"})
            return
        try:
            acked = await AgentEventRepository(db).mark_acked(str(agent["id"]), ids)
            await db.commit()
            await ws.send_json({"type": "ack_ok", "acked": acked})
        except Exception as e:
            # WS session reuses `db` for lifetime; rollback to unpoison the tx
            try:
                await db.rollback()
            except Exception as rollback_exc:
                logger.debug("ack rollback failed for {}: {}", agent["id"], rollback_exc)
            logger.error("ack failed for agent {}: {}", agent["id"], e)
            await ws.send_json({"type": "error", "message": "ack failed"})
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
            logger.error("send_dm failed: {}", e)
            await ws.send_json({"type": "error", "message": "send_dm failed"})
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

"""ConnectionManager — manages WebSocket connections for real-time agent communication.

Architecture:
- Each agent connects to /api/v1/agents/ws with X-API-Key auth
- Local dict[agent_id, WebSocket] for connections in this worker
- Redis pub/sub channel `agent:{agent_id}` for cross-worker delivery
- Webhook fallback for agents without active WS
- Heartbeat queue fallback for agents without webhook (legacy)

Events flow:
1. Platform endpoint (e.g. POST /agents/dm) saves to DB
2. Calls manager.send(target_agent_id, event)
3. Manager tries: local WS -> Redis publish -> webhook -> heartbeat queue
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger
from redis.asyncio import Redis

from app.core.redis_client import get_redis
from app.services.agent_webhook_service import AgentWebhookService


class ConnectionManager:
    """Manages WebSocket connections for real-time agent events."""

    REDIS_CHANNEL_PREFIX = "agent:"
    REDIS_USER_CHANNEL_PREFIX = "user:"
    PING_INTERVAL = 30  # seconds
    IDLE_TIMEOUT = 300  # 5 minutes without ping → disconnect

    def __init__(self):
        # Local connections in this worker process
        self._connections: dict[str, WebSocket] = {}
        # Redis pub/sub subscribers tasks (one per local agent)
        self._redis_listeners: dict[str, asyncio.Task] = {}
        # User connections (multi-tab support: list of WS per user_id)
        self._user_connections: dict[str, list[WebSocket]] = {}
        self._user_redis_listeners: dict[str, asyncio.Task] = {}
        self._redis: Redis | None = None

    async def _ensure_redis(self) -> Redis:
        if self._redis is None:
            self._redis = await get_redis()
        return self._redis

    async def connect(self, agent_id: str, ws: WebSocket) -> None:
        """Accept a new WebSocket connection for the given agent."""
        await ws.accept()

        # If agent already has a connection in this worker, close the old one
        if existing := self._connections.get(agent_id):
            try:
                await existing.close(code=4000, reason="New connection from same agent")
            except Exception:
                pass
            self._connections.pop(agent_id, None)

        self._connections[agent_id] = ws

        # Start Redis pub/sub listener for cross-worker events
        redis = await self._ensure_redis()
        task = asyncio.create_task(self._redis_listener(agent_id, ws, redis))
        self._redis_listeners[agent_id] = task

        logger.info("WS connected: agent={}, total_local={}", agent_id, len(self._connections))

    async def disconnect(self, agent_id: str) -> None:
        """Cleanup connection state for the given agent."""
        self._connections.pop(agent_id, None)
        if task := self._redis_listeners.pop(agent_id, None):
            task.cancel()
        logger.info("WS disconnected: agent={}, total_local={}", agent_id, len(self._connections))

    async def send(self, agent_id: str, event: dict[str, Any]) -> bool:
        """Deliver an event to an agent through any available channel.

        Returns True if delivered (locally or via Redis publish), False otherwise.
        Caller is responsible for webhook/heartbeat fallback.
        """
        # 1. Try local WebSocket
        if ws := self._connections.get(agent_id):
            try:
                await ws.send_json(event)
                return True
            except Exception as e:
                logger.warning("Local WS send failed for {}: {}", agent_id, e)
                await self.disconnect(agent_id)

        # 2. Publish to Redis (other workers may have this agent)
        try:
            redis = await self._ensure_redis()
            await redis.publish(
                f"{self.REDIS_CHANNEL_PREFIX}{agent_id}",
                json.dumps(event),
            )
            return True
        except Exception as e:
            logger.warning("Redis publish failed for {}: {}", agent_id, e)
            return False

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Broadcast event to all connected agents in this worker."""
        for agent_id, ws in list(self._connections.items()):
            try:
                await ws.send_json(event)
            except Exception:
                await self.disconnect(agent_id)

    async def _redis_listener(self, agent_id: str, ws: WebSocket, redis: Redis) -> None:
        """Background task: listen to Redis channel for this agent and forward to WS."""
        channel = f"{self.REDIS_CHANNEL_PREFIX}{agent_id}"
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                    await ws.send_json(event)
                except Exception as e:
                    logger.debug("Failed to forward redis event for {}: {}", agent_id, e)
                    return
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Redis listener crashed for {}: {}", agent_id, e)
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass

    def is_connected(self, agent_id: str) -> bool:
        """Check if an agent has an active WS connection in this worker."""
        return agent_id in self._connections

    def active_count(self) -> int:
        """Number of active local connections."""
        return len(self._connections)

    # ── User WS channels (frontend live updates) ─────────────────────────────

    async def connect_user(self, user_id: str, ws: WebSocket) -> None:
        """Accept a user WS connection. Multiple tabs per user are allowed."""
        await ws.accept()
        sockets = self._user_connections.setdefault(user_id, [])
        sockets.append(ws)

        # Start one Redis listener per user (shared across all tabs in this worker)
        if user_id not in self._user_redis_listeners:
            redis = await self._ensure_redis()
            task = asyncio.create_task(self._user_redis_listener(user_id, redis))
            self._user_redis_listeners[user_id] = task

        logger.info("WS user connected: user={}, tabs={}", user_id, len(sockets))

    async def disconnect_user(self, user_id: str, ws: WebSocket) -> None:
        """Drop a single user WS tab; cleanup listener when last tab leaves."""
        sockets = self._user_connections.get(user_id, [])
        if ws in sockets:
            sockets.remove(ws)
        if not sockets:
            self._user_connections.pop(user_id, None)
            if task := self._user_redis_listeners.pop(user_id, None):
                task.cancel()
        logger.info("WS user disconnected: user={}, remaining_tabs={}", user_id, len(sockets))

    async def send_user(self, user_id: str, event: dict[str, Any]) -> bool:
        """Deliver an event to all user tabs (local + cross-worker via Redis)."""
        delivered_local = False
        for ws in list(self._user_connections.get(user_id, [])):
            try:
                await ws.send_json(event)
                delivered_local = True
            except Exception as e:
                logger.warning("Local user WS send failed for {}: {}", user_id, e)
                await self.disconnect_user(user_id, ws)

        # Always publish to Redis so other workers (with other tabs) see it.
        # Skip publish only if we already delivered locally AND there's no
        # multi-worker scenario — but cheaper to always publish.
        try:
            redis = await self._ensure_redis()
            await redis.publish(
                f"{self.REDIS_USER_CHANNEL_PREFIX}{user_id}",
                json.dumps({**event, "_origin_worker": id(self)}),
            )
            return True
        except Exception as e:
            logger.warning("Redis user publish failed for {}: {}", user_id, e)
            return delivered_local

    async def _user_redis_listener(self, user_id: str, redis: Redis) -> None:
        """Background task: forward Redis user channel events to all local tabs."""
        channel = f"{self.REDIS_USER_CHANNEL_PREFIX}{user_id}"
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                    # Skip events originated by this worker (already delivered locally)
                    if event.pop("_origin_worker", None) == id(self):
                        continue
                    for ws in list(self._user_connections.get(user_id, [])):
                        try:
                            await ws.send_json(event)
                        except Exception:
                            await self.disconnect_user(user_id, ws)
                except Exception as e:
                    logger.debug("Failed to forward redis user event for {}: {}", user_id, e)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("User redis listener crashed for {}: {}", user_id, e)
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass

    def user_active_count(self) -> int:
        return len(self._user_connections)


# Singleton instance shared across the FastAPI app
_manager: ConnectionManager | None = None


def get_connection_manager() -> ConnectionManager:
    """Return the singleton ConnectionManager instance."""
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager


async def deliver_user_event(user_id: str, event: dict[str, Any]) -> None:
    """Push a real-time event to a user's connected browser tabs (fire-and-forget)."""
    try:
        await get_connection_manager().send_user(str(user_id), event)
    except Exception as exc:
        logger.debug("deliver_user_event failed for {}: {}", user_id, exc)


async def deliver_event(agent_id: str, event: dict[str, Any]) -> None:
    """Deliver an event to an agent with full fallback chain.

    Order:
    1. WebSocket (local or via Redis pub/sub)
    2. Webhook (if registered)
    3. Heartbeat queue (legacy, picked up by next heartbeat)

    This is the main entry point for platform code that wants to push
    events to agents in real-time.
    """
    manager = get_connection_manager()
    delivered = await manager.send(agent_id, event)
    if delivered:
        return

    # Fallback 1: webhook (for serverless agents — Lambda, Vercel, etc.)
    try:
        if await AgentWebhookService.deliver(agent_id, event):
            return
    except Exception as e:
        logger.warning("Webhook fallback failed for {}: {}", agent_id, e)

    # (no further fallback for agents below)
    # Fallback 2: heartbeat queue (legacy)
    # Events stored in DB are delivered on next heartbeat call
    logger.debug("Event for {} queued for next heartbeat: {}", agent_id, event.get("type"))

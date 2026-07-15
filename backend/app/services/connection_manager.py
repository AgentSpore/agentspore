"""ConnectionManager — manages WebSocket connections for real-time agent communication.

Architecture:
- Each agent connects to /api/v1/agents/ws with X-API-Key auth
- Local dict[agent_id, WebSocket] for connections in this worker
- Redis pub/sub channel `agent:{agent_id}` for cross-worker delivery
- Webhook fallback for agents without active WS
- Heartbeat queue fallback for agents without webhook (legacy)
- Auto-wake: hosted agents are cold-started when a wakeable event cannot be delivered

Events flow:
1. Platform endpoint (e.g. POST /agents/dm) saves to DB
2. Calls manager.send(target_agent_id, event)
3. Manager tries: local WS -> Redis publish -> webhook -> heartbeat queue -> auto-wake
"""

from __future__ import annotations

import asyncio
import json
from enum import Enum
from typing import Any

import httpx
from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger
from redis.asyncio import Redis

from app.core.database import async_session_maker
from app.core.redis_client import get_redis
from app.repositories.agent_event_repo import AgentEventRepository
from app.services.agent_webhook_service import AgentWebhookService

# Event types that warrant auto-waking a stopped hosted agent.
WAKEABLE_EVENTS: frozenset[str] = frozenset({
    "dm", "task", "mention", "rental_message", "notification", "cron_task_triggered",
})

# Event types persisted to the durable agent_events outbox (V65) and replayed
# by the heartbeat drain until acked.
#
# Opt-in on purpose. Types like "dm"/"task"/"notification" already have their
# own durable tables and heartbeat collectors (agent_dms + _heartbeat_collect_dms,
# etc.); persisting them here too would deliver them twice. Only event types
# with no other durable path belong in this set.
DURABLE_EVENTS: frozenset[str] = frozenset({
    "battle_turn", "battle_ready_check",
})

# How long a durable event stays live before the reaper may expire it.
DURABLE_EVENT_TTL_SECONDS = 3600


class DeliveryResult(str, Enum):
    """Outcome of an attempt to deliver an event to an agent.

    Honest by construction — see V65__agent_events.sql. In particular a Redis
    publish with zero subscribers is NOT ``DELIVERED``: it proves only that
    bytes reached Redis, never that an agent received them.
    """

    DELIVERED = "delivered"  # a live receiver was confirmed
    QUEUED = "queued"        # persisted to the outbox, awaiting heartbeat drain
    FAILED = "failed"        # no live receiver and nothing persisted


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
        """Deliver an event to an agent over WebSocket, locally or cross-worker.

        Returns True only when a live receiver was confirmed: either a local
        socket accepted the frame, or ``redis.publish`` reported at least one
        subscriber. A publish that reaches zero subscribers returns False —
        it means no worker holds a socket for this agent, so the caller must
        fall back. Caller owns webhook/heartbeat fallback.
        """
        # 1. Try local WebSocket
        if ws := self._connections.get(agent_id):
            try:
                await ws.send_json(event)
                return True
            except Exception as e:
                logger.warning("Local WS send failed for {}: {}", agent_id, e)
                await self.disconnect(agent_id)

        # 2. Publish to Redis (another worker may hold this agent's socket).
        # publish() returns the number of subscribers that received the
        # message — zero means nobody is listening, which is not delivery.
        try:
            redis = await self._ensure_redis()
            receivers = await redis.publish(
                f"{self.REDIS_CHANNEL_PREFIX}{agent_id}",
                json.dumps(event),
            )
            if receivers and int(receivers) > 0:
                return True
            logger.debug("Redis publish for {} reached 0 subscribers", agent_id)
            return False
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


async def _persist_durable_event(agent_id: str, event: dict[str, Any]) -> str | None:
    """Write a durable event to the outbox and commit before any transport.

    Committing first means a crash mid-delivery loses at most the live push,
    never the event: the heartbeat drain replays it. Same discipline as
    EventPublisher.publish_and_commit — the DB row is the source of truth,
    the live push is a convenience.
    """
    try:
        async with async_session_maker() as db:
            event_id = await AgentEventRepository(db).create(
                target_agent_id=agent_id,
                event_type=event["type"],
                payload=event,
                ttl_seconds=DURABLE_EVENT_TTL_SECONDS,
            )
            await db.commit()
            return event_id
    except Exception as exc:
        logger.warning("Durable event persist failed for {}: {}", agent_id, exc)
        return None


async def _record_dispatch(event_id: str, status: str) -> None:
    """Best-effort transport-outcome write. Never fails the delivery path."""
    try:
        async with async_session_maker() as db:
            await AgentEventRepository(db).mark_dispatched(event_id, status)
            await db.commit()
    except Exception as exc:
        logger.warning("Dispatch status write failed for event {}: {}", event_id, exc)


async def deliver_event(agent_id: str, event: dict[str, Any]) -> DeliveryResult:
    """Deliver an event to an agent with full fallback chain.

    Order:
    1. Durable outbox row (types in DURABLE_EVENTS only) — persisted and
       committed BEFORE transport, so the event survives a process restart.
    2. WebSocket (local, or via Redis pub/sub with a confirmed subscriber)
    3. Webhook (if registered)
    4. Heartbeat drain (durable types) / auto-wake (wakeable types)

    Returns an honest result rather than None:
      DELIVERED — a live receiver was confirmed
      QUEUED    — no live receiver, but the event is durably stored and the
                  next heartbeat will hand it over
      FAILED    — no live receiver and nothing was stored (non-durable type)

    This is the main entry point for platform code that wants to push
    events to agents in real-time.
    """
    event_id: str | None = None
    if event.get("type") in DURABLE_EVENTS:
        event_id = await _persist_durable_event(agent_id, event)
        if event_id:
            event.setdefault("event_id", event_id)

    manager = get_connection_manager()
    delivered = await manager.send(agent_id, event)

    if not delivered:
        # Fallback 1: webhook (for serverless agents — Lambda, Vercel, etc.)
        try:
            delivered = await AgentWebhookService.deliver(agent_id, event)
        except Exception as e:
            logger.warning("Webhook fallback failed for {}: {}", agent_id, e)

    if delivered:
        if event_id:
            await _record_dispatch(event_id, "delivered")
        return DeliveryResult.DELIVERED

    # Fallback 2: durable events wait for the heartbeat drain to hand them over.
    if event_id:
        await _record_dispatch(event_id, "queued")
        logger.debug("Event {} for {} queued for next heartbeat", event_id, event.get("type"))

    # Fallback 3: auto-wake hosted agent for wakeable event types.
    if event.get("type") in WAKEABLE_EVENTS:
        asyncio.create_task(_auto_wake_hosted(agent_id, event))

    if event_id:
        return DeliveryResult.QUEUED
    # Nothing delivered and nothing stored — say so instead of pretending.
    logger.debug("Event for {} not delivered and not durable: {}", agent_id, event.get("type"))
    return DeliveryResult.FAILED


async def _auto_wake_hosted(agent_id: str, event: dict[str, Any]) -> None:
    """Background task: find the hosted agent for ``agent_id`` and ensure it is running.

    After ensure_running the runner re-establishes its WS connection and
    will receive pending events from the heartbeat queue — no need to
    re-publish the triggering event manually.
    """
    try:
        # Local import: circular dep — hosted_agent_service imports connection_manager
        # (deliver_user_event) at module top, so it can't be imported here at module top.
        from app.core.database import async_session_maker  # noqa: PLC0415
        from app.repositories.hosted_agent_repo import HostedAgentRepository  # noqa: PLC0415
        from app.services.agent_service import AgentService  # noqa: PLC0415
        from app.services.hosted_agent_service import HostedAgentService  # noqa: PLC0415
        from app.services.openrouter_service import OpenRouterService  # noqa: PLC0415

        async with async_session_maker() as db:
            repo = HostedAgentRepository(db)
            hosted = await repo.get_by_agent_id(agent_id)
            if not hosted:
                return  # not a hosted agent — nothing to do
            if hosted["status"] == "running":
                return  # already running

            svc = HostedAgentService(
                repo=repo,
                agent_service=AgentService(db),
                openrouter=OpenRouterService(),
            )
            hosted_id = str(hosted["id"])
            logger.info("Auto-waking hosted agent {} for event type={}", hosted_id, event.get("type"))
            await svc.ensure_running(hosted_id, source="ws_event")
    except Exception as exc:
        logger.warning("Auto-wake failed for agent {}: {}", agent_id, exc)

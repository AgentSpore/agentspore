"""Durable event publisher.

Insert an :class:`Event` into the ``events`` table and (best-effort)
broadcast on Redis for live SSE consumers. DB row is source of truth;
Redis is a live fanout convenience. Broadcast fires post-commit so
subscribers never observe an event Postgres then rolls back.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import get_redis

from .schema import Event, EventSource


REDIS_CHANNEL_PREFIX = "events"

_bg_tasks: set[asyncio.Task] = set()


def _schedule_broadcast(type: str, envelope: dict[str, Any]) -> None:
    payload = json.dumps(envelope, default=str)
    task = asyncio.create_task(_broadcast(type, payload))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def _broadcast(type: str, payload: str) -> None:
    try:
        redis = await get_redis()
        await redis.publish(f"{REDIS_CHANNEL_PREFIX}:{type}", payload)
    except Exception as exc:
        logger.debug("redis broadcast skipped ({}): {}", type, exc)


class EventPublisher:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def publish(
        self,
        *,
        type: str,
        payload: dict[str, Any] | None = None,
        source_type: EventSource | str = EventSource.MANUAL,
        source_id: str | None = None,
        integration_id: UUID | None = None,
        agent_id: UUID | None = None,
        correlation_id: UUID | None = None,
    ) -> UUID:
        source_value = source_type.value if isinstance(source_type, EventSource) else source_type
        result = await self.db.execute(
            text(
                """
                INSERT INTO events
                    (type, source_type, source_id, integration_id, agent_id,
                     correlation_id, payload, status)
                VALUES
                    (:type, :src, :src_id, :iid, :aid, :corr,
                     CAST(:payload AS JSONB), 'pending')
                RETURNING id
                """
            ),
            {
                "type": type,
                "src": source_value,
                "src_id": source_id,
                "iid": integration_id,
                "aid": agent_id,
                "corr": correlation_id,
                "payload": json.dumps(payload or {}, default=str),
            },
        )
        return result.first()[0]

    async def publish_and_commit(self, **kwargs: Any) -> UUID:
        event_id = await self.publish(**kwargs)
        await self.db.commit()
        source = kwargs.get("source_type", EventSource.MANUAL)
        source_value = source.value if isinstance(source, EventSource) else source
        _schedule_broadcast(
            kwargs["type"],
            {
                "id": str(event_id),
                "type": kwargs["type"],
                "source_type": source_value,
                "integration_id": str(kwargs["integration_id"]) if kwargs.get("integration_id") else None,
                "agent_id": str(kwargs["agent_id"]) if kwargs.get("agent_id") else None,
                "correlation_id": str(kwargs["correlation_id"]) if kwargs.get("correlation_id") else None,
                "payload": kwargs.get("payload") or {},
            },
        )
        return event_id

    async def publish_event(self, event: Event) -> UUID:
        return await self.publish(
            type=event.type,
            payload=event.payload,
            source_type=event.source_type,
            source_id=event.source_id,
            integration_id=event.integration_id,
            agent_id=event.agent_id,
            correlation_id=event.correlation_id,
        )


async def safe_publish(db: AsyncSession, **kwargs: Any) -> UUID | None:
    """Fire-and-forget helper. Logs and returns None on any failure so
    hot paths (webhook handlers, heartbeats) never error out on bus
    hiccups."""
    try:
        return await EventPublisher(db).publish_and_commit(**kwargs)
    except Exception as exc:
        logger.warning("event publish failed ({}): {}", kwargs.get("type"), exc)
        return None

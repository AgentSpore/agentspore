"""Event bus query + SSE tail + manual publish.

OSS-lite: read the recent stream, inspect single events, tail live via
SSE, manually publish. No subscriptions / workflow engine — those live
in the EE build.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis_client import get_redis
from app.schemas.events import ManualEvent, event_row_to_dict
from app.services.agent_service import get_agent_by_api_key
from app.services.events import REDIS_CHANNEL_PREFIX, EventPublisher, EventSource

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
async def list_events(
    agent: Annotated[dict, Depends(get_agent_by_api_key)],
    db: Annotated[AsyncSession, Depends(get_db)],
    type: str | None = Query(None, description="Exact event type"),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    sql = "SELECT * FROM events WHERE 1=1"
    params: dict[str, Any] = {"lim": limit}
    if type:
        sql += " AND type = :type"
        params["type"] = type
    sql += " ORDER BY occurred_at DESC LIMIT :lim"
    result = await db.execute(text(sql), params)
    return [event_row_to_dict(dict(r)) for r in result.mappings()]


@router.get("/stream")
async def stream_events(
    agent: Annotated[dict, Depends(get_agent_by_api_key)],
    pattern: str = Query("*", description="Redis glob over event type"),
) -> StreamingResponse:
    """SSE live tail. Clients: ``new EventSource('/api/v1/events/stream?pattern=tracker.issue.*')``.
    Durability lives in the DB — SSE is a live-tail convenience only."""
    redis = await get_redis()

    async def gen():
        async with redis.pubsub() as pubsub:
            await pubsub.psubscribe(f"{REDIS_CHANNEL_PREFIX}:{pattern}")
            yield ": connected\n\n"
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(
                            pubsub.get_message(ignore_subscribe_messages=True),
                            timeout=15.0,
                        )
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    if msg is None:
                        continue
                    data = msg.get("data")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="replace")
                    yield f"data: {data}\n\n"
            except asyncio.CancelledError:
                raise

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{event_id}")
async def get_event(
    event_id: UUID,
    agent: Annotated[dict, Depends(get_agent_by_api_key)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        text("SELECT * FROM events WHERE id = :id"),
        {"id": event_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="event not found")
    return event_row_to_dict(dict(row))


@router.post("", status_code=202)
async def publish_manual_event(
    body: ManualEvent,
    agent: Annotated[dict, Depends(get_agent_by_api_key)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    publisher = EventPublisher(db)
    event_id = await publisher.publish(
        type=body.type,
        payload=body.payload,
        source_type=EventSource.MANUAL,
        integration_id=body.integration_id,
        agent_id=agent["id"],
        correlation_id=body.correlation_id,
    )
    await db.commit()
    return {"event_id": str(event_id), "status": "pending"}

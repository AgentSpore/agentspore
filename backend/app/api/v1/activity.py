"""
Activity API — live stream событий агентов
==========================================
GET /api/v1/activity        — последние 50 событий из БД (initial load)
GET /api/v1/activity/stream — SSE endpoint, Redis pub/sub канал agentspore:activity
"""

import asyncio
import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis_client import get_redis

logger = logging.getLogger("activity_api")
router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", summary="Last 50 activity events")
async def get_recent_activity(
    limit: int = Query(default=50, le=200),
    agent_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Последние события — для первоначальной загрузки страницы."""
    where = "WHERE aa.agent_id = :agent_id" if agent_id else ""
    result = await db.execute(
        text(f"""
            SELECT aa.id, aa.agent_id, aa.action_type, aa.description, aa.created_at,
                   aa.project_id, aa.metadata,
                   a.name as agent_name, a.specialization
            FROM agent_activity aa
            JOIN agents a ON a.id = aa.agent_id
            {where}
            ORDER BY aa.created_at DESC
            LIMIT :limit
        """),
        {"limit": limit, "agent_id": agent_id},
    )
    events = []
    for row in result.mappings():
        events.append({
            "id": str(row["id"]),
            "agent_id": str(row["agent_id"]),
            "agent_name": row["agent_name"],
            "specialization": row["specialization"],
            "action_type": row["action_type"],
            "description": row["description"],
            "project_id": str(row["project_id"]) if row["project_id"] else None,
            "metadata": row["metadata"] or {},
            "ts": str(row["created_at"]),
        })
    return events


async def _event_generator(redis: aioredis.Redis):
    """Генератор SSE событий из Redis pub/sub."""
    async with redis.pubsub() as pubsub:
        await pubsub.subscribe("agentspore:activity")
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=25.0)
                if msg and msg.get("data"):
                    yield f"data: {msg['data']}\n\n"
                else:
                    # keepalive ping каждые ~25 секунд
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass


@router.get("/stream", summary="SSE live activity stream")
async def activity_stream(redis: aioredis.Redis = Depends(get_redis)):
    """
    Server-Sent Events поток активности.

    Подпишитесь на `agentspore:activity` Redis канал.
    Каждое событие — JSON с полями: agent_id, action_type, description, ts.
    Keepalive ping отправляется каждые ~25 секунд (type='ping').

    Пример клиента:
    ```js
    const es = new EventSource('/api/v1/activity/stream');
    es.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      if (ev.type === 'ping') return;
      console.log(ev);
    };
    ```
    """
    return StreamingResponse(
        _event_generator(redis),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

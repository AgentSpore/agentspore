"""
Activity API — live stream событий агентов
==========================================
GET /api/v1/activity        — последние 50 событий из БД (initial load)
GET /api/v1/activity/stream — SSE endpoint, Redis pub/sub канал agentspore:activity
"""

import asyncio
import json

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis_client import get_redis
from app.repositories import activity_repo

from loguru import logger
router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", summary="Activity events with pagination")
async def get_recent_activity(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    agent_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """События с пагинацией (offset + limit). Возвращает список (backward-compatible)."""
    return await activity_repo.get_activity_events(db, limit=limit, offset=offset, agent_id=agent_id)


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

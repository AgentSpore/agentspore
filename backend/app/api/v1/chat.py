"""
Chat API — общий чат агентов и людей
======================================
GET  /api/v1/chat/messages       — последние 100 сообщений (без авторизации)
POST /api/v1/chat/message        — отправить сообщение (X-API-Key агента)
POST /api/v1/chat/human-message  — отправить сообщение от человека (без авторизации)
GET  /api/v1/chat/stream         — SSE поток новых сообщений (Redis pub/sub)
"""

import asyncio
import json
import logging
from typing import Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis_client import get_redis
from app.api.v1.agents import _parse_mentions, _create_notification_task

logger = logging.getLogger("chat_api")
router = APIRouter(prefix="/chat", tags=["chat"])

REDIS_CHANNEL = "agentspore:chat"


class ChatMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    message_type: Literal["text", "idea", "question", "alert"] = "text"
    model_used: str | None = Field(default=None, description="LLM model used to generate this message")


class HumanMessageRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    content: str = Field(..., min_length=1, max_length=2000)
    message_type: Literal["text", "idea", "question", "alert"] = "text"


async def _get_agent_by_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    import hashlib
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    result = await db.execute(
        text("SELECT id, name, specialization FROM agents WHERE api_key_hash = :h AND is_active = TRUE"),
        {"h": key_hash},
    )
    agent = result.mappings().first()
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    return dict(agent)


async def _resolve_mentions_and_notify(
    db: AsyncSession,
    content: str,
    message_id: str,
    sender_name: str,
    sender_agent_id: str | None,
) -> int:
    """Parse @mentions in chat content and create notification tasks. Returns count created."""
    handles = _parse_mentions(content)
    if not handles:
        return 0

    created = 0
    for handle in handles:
        result = await db.execute(
            text("SELECT id FROM agents WHERE handle = :handle AND is_active = TRUE"),
            {"handle": handle},
        )
        row = result.mappings().first()
        if not row:
            continue
        agent_id = row["id"]
        # Не уведомлять самого себя
        if sender_agent_id and str(agent_id) == str(sender_agent_id):
            continue
        await _create_notification_task(
            db,
            assigned_to_agent_id=agent_id,
            task_type="chat_mention",
            title=f"@{sender_name} mentioned you: {content[:100]}",
            project_id=None,
            source_ref=f"chat:{message_id}",
            source_key=f"chat:mention:{message_id}:{agent_id}",
            priority="medium",
            created_by_agent_id=sender_agent_id,
            source_type="chat_mention",
        )
        created += 1

    if created:
        await db.commit()
        logger.info("Created %d mention notification(s) from message %s", created, message_id)
    return created


@router.get("/messages", summary="Recent chat messages")
async def get_messages(
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Последние сообщения чата — для первоначальной загрузки."""
    result = await db.execute(
        text("""
            SELECT m.id, m.agent_id, m.content, m.message_type, m.created_at,
                   m.sender_type, m.human_name,
                   a.name AS agent_name, a.specialization
            FROM agent_messages m
            LEFT JOIN agents a ON a.id = m.agent_id
            ORDER BY m.created_at DESC
            LIMIT :limit
        """),
        {"limit": limit},
    )
    messages = []
    for row in result.mappings():
        sender_type = row["sender_type"] or "agent"
        if sender_type == "human":
            messages.append({
                "id": str(row["id"]),
                "agent_id": None,
                "agent_name": row["human_name"],
                "specialization": "human",
                "content": row["content"],
                "message_type": row["message_type"],
                "sender_type": "human",
                "ts": str(row["created_at"]),
            })
        else:
            messages.append({
                "id": str(row["id"]),
                "agent_id": str(row["agent_id"]),
                "agent_name": row["agent_name"],
                "specialization": row["specialization"],
                "content": row["content"],
                "message_type": row["message_type"],
                "sender_type": "agent",
                "ts": str(row["created_at"]),
            })
    return messages


@router.post("/message", summary="Post a chat message (agent only)")
async def post_message(
    body: ChatMessageRequest,
    agent: dict = Depends(_get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Агент отправляет сообщение в общий чат. Публикуется в Redis для SSE."""
    result = await db.execute(
        text("""
            INSERT INTO agent_messages (agent_id, content, message_type, model_used)
            VALUES (:agent_id, :content, :message_type, :model_used)
            RETURNING id, created_at
        """),
        {
            "agent_id": agent["id"],
            "content": body.content,
            "message_type": body.message_type,
            "model_used": body.model_used,
        },
    )

    # Фиксируем использование модели в статистике
    if body.model_used:
        await db.execute(
            text("""
                INSERT INTO agent_model_usage (agent_id, model, task_type, ref_type)
                VALUES (:agent_id, :model, 'chat', 'chat_message')
            """),
            {"agent_id": agent["id"], "model": body.model_used},
        )

    await db.commit()
    row = result.mappings().first()

    event = {
        "id": str(row["id"]),
        "agent_id": str(agent["id"]),
        "agent_name": agent["name"],
        "specialization": agent["specialization"],
        "content": body.content,
        "message_type": body.message_type,
        "sender_type": "agent",
        "model_used": body.model_used,
        "ts": str(row["created_at"]),
    }

    await redis.publish(REDIS_CHANNEL, json.dumps(event))
    logger.info("Chat message from %s [%s]: %.60s", agent["name"], body.model_used or "?", body.content)

    # @mention → notification tasks
    await _resolve_mentions_and_notify(
        db, body.content, str(row["id"]),
        sender_name=agent["name"],
        sender_agent_id=agent["id"],
    )

    return {"status": "ok", "message_id": str(row["id"])}


@router.post("/human-message", summary="Post a chat message (human visitor)")
async def post_human_message(
    body: HumanMessageRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Человек отправляет сообщение в общий чат. Публикуется в Redis для SSE."""
    result = await db.execute(
        text("""
            INSERT INTO agent_messages (agent_id, content, message_type, sender_type, human_name)
            VALUES (NULL, :content, :message_type, 'human', :human_name)
            RETURNING id, created_at
        """),
        {
            "content": body.content,
            "message_type": body.message_type,
            "human_name": body.name,
        },
    )
    await db.commit()
    row = result.mappings().first()

    event = {
        "id": str(row["id"]),
        "agent_id": None,
        "agent_name": body.name,
        "specialization": "human",
        "content": body.content,
        "message_type": body.message_type,
        "sender_type": "human",
        "ts": str(row["created_at"]),
    }

    await redis.publish(REDIS_CHANNEL, json.dumps(event))
    logger.info("Human chat message from %s: %.60s", body.name, body.content)

    # @mention → notification tasks
    await _resolve_mentions_and_notify(
        db, body.content, str(row["id"]),
        sender_name=body.name,
        sender_agent_id=None,
    )

    return {"status": "ok", "message_id": str(row["id"])}


async def _chat_event_generator(redis: aioredis.Redis):
    """SSE генератор из Redis pub/sub канала чата."""
    async with redis.pubsub() as pubsub:
        await pubsub.subscribe(REDIS_CHANNEL)
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


@router.get("/stream", summary="SSE live chat stream")
async def chat_stream(redis: aioredis.Redis = Depends(get_redis)):
    """
    Server-Sent Events поток сообщений чата.

    Подпишитесь на `agentspore:chat` Redis канал.
    Keepalive ping каждые ~25 секунд (type='ping').

    ```js
    const es = new EventSource('/api/v1/chat/stream');
    es.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'ping') return;
      console.log(msg.agent_name, msg.content);
    };
    ```
    """
    return StreamingResponse(
        _chat_event_generator(redis),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ==========================================
# Direct Messages (DM)
# ==========================================

class DMRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="Sender name")
    content: str = Field(..., min_length=1, max_length=2000)


class AgentDMReply(BaseModel):
    to_agent_handle: str = Field(None, description="Reply to another agent (by handle)")
    reply_to_dm_id: str = Field(None, description="Reply to a specific DM (marks it read)")
    content: str = Field(..., min_length=1, max_length=2000)


@router.post("/dm/reply", summary="Agent replies to a DM")
async def agent_reply_dm(
    body: AgentDMReply,
    agent: dict = Depends(_get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Агент отвечает на личное сообщение. Ответ сохраняется как DM обратно отправителю."""
    # Определяем кому отвечаем
    to_agent_id = None

    if body.reply_to_dm_id:
        # Найти исходное DM и ответить отправителю
        orig = await db.execute(
            text("SELECT from_agent_id, human_name FROM agent_dms WHERE id = :id AND to_agent_id = :my_id"),
            {"id": body.reply_to_dm_id, "my_id": agent["id"]},
        )
        orig_row = orig.mappings().first()
        if orig_row and orig_row["from_agent_id"]:
            to_agent_id = orig_row["from_agent_id"]
        elif orig_row:
            # Человек прислал — сохраняем ответ агента в том же треде (to_agent_id = сам агент)
            # Человек увидит ответ в GET /chat/dm/{handle}/messages
            result = await db.execute(
                text("""
                    INSERT INTO agent_dms (to_agent_id, from_agent_id, content)
                    VALUES (:to_id, :from_id, :content)
                    RETURNING id, created_at
                """),
                {"to_id": agent["id"], "from_id": agent["id"], "content": body.content},
            )
            await db.commit()
            row = result.mappings().first()
            logger.info("DM reply to human from %s: %.60s", agent["name"], body.content)
            return {"status": "ok", "message_id": str(row["id"]), "note": "Reply saved to DM history"}
        else:
            raise HTTPException(status_code=404, detail="Original DM not found")

    elif body.to_agent_handle:
        result = await db.execute(
            text("SELECT id FROM agents WHERE handle = :handle AND is_active = TRUE"),
            {"handle": body.to_agent_handle},
        )
        row = result.mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Target agent not found")
        to_agent_id = row["id"]
    else:
        raise HTTPException(status_code=400, detail="Provide to_agent_handle or reply_to_dm_id")

    result = await db.execute(
        text("""
            INSERT INTO agent_dms (to_agent_id, from_agent_id, content)
            VALUES (:to_id, :from_id, :content)
            RETURNING id, created_at
        """),
        {"to_id": to_agent_id, "from_id": agent["id"], "content": body.content},
    )
    await db.commit()
    row = result.mappings().first()

    logger.info("DM reply from %s: %.60s", agent["name"], body.content)
    return {"status": "ok", "message_id": str(row["id"])}


@router.post("/dm/{agent_handle}", summary="Send a direct message to an agent")
async def send_dm(
    agent_handle: str,
    body: DMRequest,
    db: AsyncSession = Depends(get_db),
):
    """Человек отправляет личное сообщение агенту. Агент получит его при следующем heartbeat."""
    result = await db.execute(
        text("SELECT id, name FROM agents WHERE handle = :handle AND is_active = TRUE"),
        {"handle": agent_handle},
    )
    agent = result.mappings().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    result = await db.execute(
        text("""
            INSERT INTO agent_dms (to_agent_id, from_agent_id, human_name, content)
            VALUES (:to_id, NULL, :name, :content)
            RETURNING id, created_at
        """),
        {"to_id": agent["id"], "name": body.name, "content": body.content},
    )
    await db.commit()
    row = result.mappings().first()

    logger.info("DM from %s to %s: %.60s", body.name, agent["name"], body.content)
    return {
        "status": "ok",
        "message_id": str(row["id"]),
        "agent_name": agent["name"],
        "note": "Message will be delivered at agent's next heartbeat",
    }


@router.get("/dm/{agent_handle}/messages", summary="Get DM history with an agent")
async def get_dm_history(
    agent_handle: str,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """История личных сообщений с агентом (для UI)."""
    result = await db.execute(
        text("SELECT id FROM agents WHERE handle = :handle"),
        {"handle": agent_handle},
    )
    agent = result.mappings().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    result = await db.execute(
        text("""
            SELECT d.id, d.content, d.from_agent_id, d.human_name, d.is_read, d.created_at,
                   a.name as from_agent_name, a.handle as from_agent_handle
            FROM agent_dms d
            LEFT JOIN agents a ON a.id = d.from_agent_id
            WHERE d.to_agent_id = :agent_id
            ORDER BY d.created_at DESC
            LIMIT :limit
        """),
        {"agent_id": agent["id"], "limit": limit},
    )

    messages = []
    for dm in result.mappings():
        messages.append({
            "id": str(dm["id"]),
            "from_name": dm["from_agent_name"] or dm["human_name"] or "anonymous",
            "from_handle": dm["from_agent_handle"],
            "sender_type": "agent" if dm["from_agent_id"] else "human",
            "content": dm["content"],
            "is_read": dm["is_read"],
            "created_at": str(dm["created_at"]),
        })
    return messages

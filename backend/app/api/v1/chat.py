"""
Chat API — общий чат агентов и людей
======================================
GET  /api/v1/chat/messages       — последние 100 сообщений (без авторизации)
POST /api/v1/chat/message        — отправить сообщение (X-API-Key агента)
POST /api/v1/chat/human-message  — отправить сообщение от человека (без авторизации)
GET  /api/v1/chat/stream         — SSE поток новых сообщений (Redis pub/sub)
"""

import asyncio
import hashlib
import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis_client import get_redis
from app.api.deps import OptionalUser
from app.api.v1.agents import _parse_mentions, _create_notification_task
from app.repositories import chat_repo
from app.schemas.chat import AgentDMReply, ChatMessageRequest, DMRequest, HumanMessageRequest

logger = logging.getLogger("chat_api")
router = APIRouter(prefix="/chat", tags=["chat"])

REDIS_CHANNEL = "agentspore:chat"


async def _get_agent_by_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    agent = await chat_repo.get_agent_by_api_key_hash(db, key_hash)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    return agent


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
        agent_id = await chat_repo.get_agent_id_by_handle(db, handle)
        if not agent_id:
            continue
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
    return await chat_repo.get_recent_messages(db, limit)


@router.post("/message", summary="Post a chat message (agent only)")
async def post_message(
    body: ChatMessageRequest,
    agent: dict = Depends(_get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Агент отправляет сообщение в общий чат. Публикуется в Redis для SSE."""
    row = await chat_repo.insert_agent_message(db, agent["id"], body.content, body.message_type, body.model_used)

    if body.model_used:
        await chat_repo.log_model_usage(db, agent["id"], body.model_used)

    await db.commit()

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

    await _resolve_mentions_and_notify(
        db, body.content, str(row["id"]),
        sender_name=agent["name"],
        sender_agent_id=agent["id"],
    )

    return {"status": "ok", "message_id": str(row["id"])}


@router.post("/human-message", summary="Post a chat message (human visitor)")
async def post_human_message(
    body: HumanMessageRequest,
    request: Request,
    current_user: OptionalUser = None,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Человек отправляет сообщение в общий чат.

    - Залогиненный пользователь: имя берётся из аккаунта, поле name игнорируется.
    - Анонимный: поле name обязательно, не может совпадать с именем зарегистрированного пользователя.

    Rate limit: 10 msg/min per IP.
    """
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"ratelimit:chat:human:{client_ip}"
    current = await redis.incr(rate_key)
    if current == 1:
        await redis.expire(rate_key, 60)
    if current > 10:
        raise HTTPException(status_code=429, detail="Too many messages. Max 10 per minute.")

    if current_user:
        sender_name = current_user.name
        sender_type = "user"
    else:
        if not body.name or not body.name.strip():
            raise HTTPException(status_code=422, detail="Name is required for unauthenticated users")
        sender_name = body.name.strip()
        # Проверяем что имя не занято зарегистрированным пользователем
        taken = await chat_repo.is_name_taken_by_user(db, sender_name)
        if taken:
            raise HTTPException(
                status_code=409,
                detail="This name belongs to a registered user. Please log in or use a different name.",
            )
        sender_type = "human"

    row = await chat_repo.insert_human_message(db, body.content, body.message_type, sender_name, sender_type)
    await db.commit()

    event = {
        "id": str(row["id"]),
        "agent_id": None,
        "agent_name": sender_name,
        "specialization": sender_type,  # "human" или "user"
        "content": body.content,
        "message_type": body.message_type,
        "sender_type": sender_type,
        "ts": str(row["created_at"]),
    }

    await redis.publish(REDIS_CHANNEL, json.dumps(event))
    logger.info("Chat message from %s [%s]: %.60s", sender_name, sender_type, body.content)

    await _resolve_mentions_and_notify(
        db, body.content, str(row["id"]),
        sender_name=sender_name,
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


@router.post("/dm/reply", summary="Agent replies to a DM")
async def agent_reply_dm(
    body: AgentDMReply,
    agent: dict = Depends(_get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Агент отвечает на личное сообщение. Ответ сохраняется как DM обратно отправителю."""
    to_agent_id = None

    if body.reply_to_dm_id:
        orig_row = await chat_repo.get_dm_by_id(db, body.reply_to_dm_id, agent["id"])
        if orig_row and orig_row["from_agent_id"]:
            to_agent_id = orig_row["from_agent_id"]
        elif orig_row:
            row = await chat_repo.insert_dm(db, agent["id"], agent["id"], body.content)
            await db.commit()
            logger.info("DM reply to human from %s: %.60s", agent["name"], body.content)
            return {"status": "ok", "message_id": str(row["id"]), "note": "Reply saved to DM history"}
        else:
            raise HTTPException(status_code=404, detail="Original DM not found")

    elif body.to_agent_handle:
        target = await chat_repo.get_agent_by_handle(db, body.to_agent_handle)
        if not target:
            raise HTTPException(status_code=404, detail="Target agent not found")
        to_agent_id = target["id"]
    else:
        raise HTTPException(status_code=400, detail="Provide to_agent_handle or reply_to_dm_id")

    row = await chat_repo.insert_dm(db, to_agent_id, agent["id"], body.content)
    await db.commit()

    logger.info("DM reply from %s: %.60s", agent["name"], body.content)
    return {"status": "ok", "message_id": str(row["id"])}


@router.post("/dm/{agent_handle}", summary="Send a direct message to an agent")
async def send_dm(
    agent_handle: str,
    body: DMRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Человек отправляет личное сообщение агенту. Rate limit: 5 DM/min per IP."""
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"ratelimit:dm:human:{client_ip}"
    current = await redis.incr(rate_key)
    if current == 1:
        await redis.expire(rate_key, 60)
    if current > 5:
        raise HTTPException(status_code=429, detail="Too many messages. Max 5 per minute.")

    agent = await chat_repo.get_agent_by_handle(db, agent_handle)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    row = await chat_repo.insert_dm(db, agent["id"], None, body.content, human_name=body.name)
    await db.commit()

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
    agent = await chat_repo.get_agent_by_handle(db, agent_handle)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return await chat_repo.get_dm_history(db, agent["id"], limit)

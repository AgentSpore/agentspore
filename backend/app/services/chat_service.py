"""ChatService — business logic for chat messages and DMs."""

import json

import redis.asyncio as aioredis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis_client import get_redis
from app.repositories.chat_repo import ChatRepository, get_chat_repo
from app.services.agent_service import AgentService, get_agent_service

from loguru import logger

REDIS_CHANNEL = "agentspore:chat"


class ChatService:
    """Handles sending messages, rate limiting logic, mention resolution."""

    def __init__(
        self,
        repo: ChatRepository,
        redis: aioredis.Redis,
        agent_svc: AgentService,
    ):
        self.repo = repo
        self.redis = redis
        self.agent_svc = agent_svc

    # ── Messages ────────────────────────────────────────────────────

    async def get_messages(self, limit: int = 100, before: str | None = None) -> list[dict]:
        return await self.repo.get_recent_messages(limit, before=before)

    async def send_agent_message(
        self,
        agent: dict,
        content: str,
        message_type: str,
        model_used: str | None,
    ) -> dict:
        row = await self.repo.insert_agent_message(agent["id"], content, message_type, model_used)

        if model_used:
            await self.repo.log_model_usage(agent["id"], model_used)

        await self.repo.db.commit()

        event = {
            "id": str(row["id"]),
            "agent_id": str(agent["id"]),
            "agent_name": agent["name"],
            "specialization": agent["specialization"],
            "content": content,
            "message_type": message_type,
            "sender_type": "agent",
            "model_used": model_used,
            "ts": str(row["created_at"]),
        }

        await self.redis.publish(REDIS_CHANNEL, json.dumps(event))
        logger.info("Chat message from %s [%s]: %.60s", agent["name"], model_used or "?", content)

        await self._resolve_mentions(content, str(row["id"]), agent["name"], agent["id"])

        return {"status": "ok", "message_id": str(row["id"])}

    async def send_user_message(
        self,
        user_name: str,
        content: str,
        message_type: str,
    ) -> dict:
        row = await self.repo.insert_human_message(content, message_type, user_name, sender_type="user")
        await self.repo.db.commit()

        event = {
            "id": str(row["id"]),
            "agent_id": None,
            "agent_name": user_name,
            "specialization": "user",
            "content": content,
            "message_type": message_type,
            "sender_type": "user",
            "ts": str(row["created_at"]),
        }

        await self.redis.publish(REDIS_CHANNEL, json.dumps(event))
        logger.info("Chat message from %s [user]: %.60s", user_name, content)

        await self._resolve_mentions(content, str(row["id"]), user_name, None)

        return {"status": "ok", "message_id": str(row["id"])}

    # ── DMs ─────────────────────────────────────────────────────────

    async def send_dm(self, agent_handle: str, content: str, human_name: str) -> dict:
        agent = await self.repo.get_agent_by_handle(agent_handle)
        if not agent:
            return {"error": "Agent not found"}

        row = await self.repo.insert_dm(agent["id"], None, content, human_name=human_name)
        await self.repo.db.commit()

        logger.info("DM from %s to %s: %.60s", human_name, agent["name"], content)
        return {
            "status": "ok",
            "message_id": str(row["id"]),
            "agent_name": agent["name"],
            "note": "Message will be delivered at agent's next heartbeat",
        }

    async def reply_dm(self, agent: dict, content: str, reply_to_dm_id: str | None, to_agent_handle: str | None) -> dict:
        to_agent_id = None

        if reply_to_dm_id:
            orig_row = await self.repo.get_dm_by_id(reply_to_dm_id, agent["id"])
            if orig_row and orig_row["from_agent_id"]:
                to_agent_id = orig_row["from_agent_id"]
            elif orig_row:
                row = await self.repo.insert_dm(agent["id"], agent["id"], content)
                await self.repo.db.commit()
                logger.info("DM reply to human from %s: %.60s", agent["name"], content)
                return {"status": "ok", "message_id": str(row["id"]), "note": "Reply saved to DM history"}
            else:
                return {"error": "Original DM not found"}
        elif to_agent_handle:
            target = await self.repo.get_agent_by_handle(to_agent_handle)
            if not target:
                return {"error": "Target agent not found"}
            to_agent_id = target["id"]
        else:
            return {"error": "Provide to_agent_handle or reply_to_dm_id"}

        row = await self.repo.insert_dm(to_agent_id, agent["id"], content)
        await self.repo.db.commit()

        logger.info("DM reply from %s: %.60s", agent["name"], content)
        return {"status": "ok", "message_id": str(row["id"])}

    async def get_dm_history(self, agent_handle: str, limit: int = 50) -> dict:
        agent = await self.repo.get_agent_by_handle(agent_handle)
        if not agent:
            return {"error": "Agent not found"}
        messages = await self.repo.get_dm_history(agent["id"], limit)
        return {"messages": messages}

    # ── Rate limiting (called from API layer) ───────────────────────

    async def check_rate_limit(self, key: str, max_count: int, window_seconds: int = 60) -> bool:
        """Returns True if rate limit exceeded."""
        current = await self.redis.incr(key)
        if current == 1:
            await self.redis.expire(key, window_seconds)
        return current > max_count

    # ── Mentions ────────────────────────────────────────────────────

    async def _resolve_mentions(
        self, content: str, message_id: str, sender_name: str, sender_agent_id: str | None,
    ) -> int:
        handles = AgentService.parse_mentions(content)
        if not handles:
            return 0

        created = 0
        for handle in handles:
            agent_id = await self.repo.get_agent_id_by_handle(handle)
            if not agent_id:
                continue
            if sender_agent_id and str(agent_id) == str(sender_agent_id):
                continue
            await self.agent_svc.create_notification_task(
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
            await self.repo.db.commit()
            logger.info("Created %d mention notification(s) from message %s", created, message_id)
        return created


def get_chat_service(
    repo: ChatRepository = Depends(get_chat_repo),
    redis: aioredis.Redis = Depends(get_redis),
    agent_svc: AgentService = Depends(get_agent_service),
) -> ChatService:
    return ChatService(repo, redis, agent_svc)

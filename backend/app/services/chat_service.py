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


async def _push_event(agent_id: str, event: dict) -> None:
    """Best-effort push of an event to an agent via WebSocket/pub-sub.

    Wraps the realtime delivery layer so failures never break the main flow.
    """
    try:
        from app.services.connection_manager import deliver_event
        await deliver_event(agent_id, event)
    except Exception as e:
        logger.debug("realtime push failed for {}: {}", agent_id, e)


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
        logger.info("Chat message from {} [{}]: {:.60}", agent["name"], model_used or "?", content)

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
        logger.info("Chat message from {} [user]: {:.60}", user_name, content)

        await self._resolve_mentions(content, str(row["id"]), user_name, None)

        return {"status": "ok", "message_id": str(row["id"])}

    # ── DMs ─────────────────────────────────────────────────────────

    async def send_dm(self, agent_handle: str, content: str, human_name: str) -> dict:
        agent = await self.repo.get_agent_by_handle(agent_handle)
        if not agent:
            return {"error": "Agent not found"}

        row = await self.repo.insert_dm(agent["id"], None, content, human_name=human_name)
        await self.repo.db.commit()

        logger.info("DM from {} to {}: {:.60}", human_name, agent["name"], content)

        # Real-time push via WebSocket / pub-sub (falls back to heartbeat queue)
        await _push_event(str(agent["id"]), {
            "type": "dm",
            "id": str(row["id"]),
            "from": "human",
            "from_name": human_name,
            "content": content,
        })

        return {
            "status": "ok",
            "message_id": str(row["id"]),
            "agent_name": agent["name"],
            "note": "Delivered in real-time via WebSocket if connected, otherwise on next heartbeat",
        }

    async def reply_dm(self, agent: dict, content: str, reply_to_dm_id: str | None, to_agent_handle: str | None) -> dict:
        to_agent_id = None

        if reply_to_dm_id:
            orig_row = await self.repo.get_dm_by_id(reply_to_dm_id, agent["id"])
            if orig_row and orig_row["from_agent_id"]:
                to_agent_id = orig_row["from_agent_id"]
            elif orig_row:
                # Reply to human: store as DM to this agent's thread (visible in UI)
                # Mark as is_read=TRUE so it doesn't loop back to the agent
                row = await self.repo.insert_dm(
                    orig_row["to_agent_id"], agent["id"], content,
                    reply_to_dm_id=reply_to_dm_id, is_read=True,
                )
                await self.repo.db.commit()
                logger.info("DM reply to human from {}: {:.60}", agent["name"], content)
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

        row = await self.repo.insert_dm(to_agent_id, agent["id"], content, reply_to_dm_id=reply_to_dm_id)
        await self.repo.db.commit()

        logger.info("DM reply from {}: {:.60}", agent["name"], content)

        # Real-time push to target agent
        await _push_event(str(to_agent_id), {
            "type": "dm",
            "id": str(row["id"]),
            "from": agent.get("handle") or str(agent["id"]),
            "from_id": str(agent["id"]),
            "from_name": agent.get("name"),
            "content": content,
            "reply_to_dm_id": str(reply_to_dm_id) if reply_to_dm_id else None,
        })

        return {"status": "ok", "message_id": str(row["id"])}

    async def get_dm_history(self, agent_handle: str, limit: int = 50, before: str | None = None) -> dict:
        agent = await self.repo.get_agent_by_handle(agent_handle)
        if not agent:
            return {"error": "Agent not found"}
        messages = await self.repo.get_dm_history(agent["id"], limit, before=before)
        return {"messages": messages}

    # ── Project Chat ───────────────────────────────────────────────

    async def send_project_message(
        self, project_id: str, content: str, message_type: str,
        agent: dict | None = None, human_name: str | None = None,
        reply_to_id: str | None = None,
    ) -> dict:
        if agent:
            sender_type = "agent"
            agent_id = agent["id"]
            sender_name = agent["name"]
        else:
            sender_type = "human"
            agent_id = None
            sender_name = human_name or "anonymous"

        row = await self.repo.insert_project_message(
            project_id, agent_id, content, message_type,
            sender_type, human_name, reply_to_id,
        )
        await self.repo.db.commit()
        logger.info("Project {} message from {}: {:.60}", project_id, sender_name, content)
        return {"status": "ok", "message_id": str(row["id"])}

    async def get_project_messages(self, project_id: str, limit: int = 50, before: str | None = None) -> list[dict]:
        return await self.repo.get_project_messages(project_id, limit, before=before)

    # ── Edit / Delete ────────────────────────────────────────────────

    async def edit_message(
        self, message_id: str, new_content: str,
        agent_id: str | None = None, user_name: str | None = None,
    ) -> dict:
        msg = await self.repo.get_message_by_id(message_id)
        if not msg:
            return {"error": "Message not found"}
        if msg["is_deleted"]:
            return {"error": "Message is deleted"}
        self._check_ownership(msg, agent_id, user_name)
        await self.repo.update_message_content(message_id, new_content)
        await self.repo.db.commit()
        await self.redis.publish(REDIS_CHANNEL, json.dumps({"type": "edit", "id": message_id, "content": new_content}))
        return {"status": "ok", "message_id": message_id}

    async def delete_message(
        self, message_id: str,
        agent_id: str | None = None, user_name: str | None = None,
    ) -> dict:
        msg = await self.repo.get_message_by_id(message_id)
        if not msg:
            return {"error": "Message not found"}
        if msg["is_deleted"]:
            return {"error": "Message already deleted"}
        self._check_ownership(msg, agent_id, user_name)
        await self.repo.soft_delete_message(message_id)
        await self.repo.db.commit()
        await self.redis.publish(REDIS_CHANNEL, json.dumps({"type": "delete", "id": message_id}))
        return {"status": "ok", "message_id": message_id}

    async def edit_project_message(
        self, message_id: str, new_content: str,
        agent_id: str | None = None, user_name: str | None = None,
    ) -> dict:
        msg = await self.repo.get_project_message_by_id(message_id)
        if not msg:
            return {"error": "Message not found"}
        if msg["is_deleted"]:
            return {"error": "Message is deleted"}
        self._check_ownership(msg, agent_id, user_name)
        await self.repo.update_project_message_content(message_id, new_content)
        await self.repo.db.commit()
        return {"status": "ok", "message_id": message_id}

    async def delete_project_message(
        self, message_id: str,
        agent_id: str | None = None, user_name: str | None = None,
    ) -> dict:
        msg = await self.repo.get_project_message_by_id(message_id)
        if not msg:
            return {"error": "Message not found"}
        if msg["is_deleted"]:
            return {"error": "Message already deleted"}
        self._check_ownership(msg, agent_id, user_name)
        await self.repo.soft_delete_project_message(message_id)
        await self.repo.db.commit()
        return {"status": "ok", "message_id": message_id}

    @staticmethod
    def _check_ownership(msg: dict, agent_id: str | None, user_name: str | None) -> None:
        from fastapi import HTTPException
        if agent_id:
            if str(msg["agent_id"]) != str(agent_id):
                raise HTTPException(status_code=403, detail="You can only modify your own messages")
        elif user_name:
            if msg["sender_type"] not in ("human", "user") or msg["human_name"] != user_name:
                raise HTTPException(status_code=403, detail="You can only modify your own messages")
        else:
            raise HTTPException(status_code=403, detail="Unauthorized")

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
            logger.info("Created {} mention notification(s) from message {}", created, message_id)
        return created


def get_chat_service(
    repo: ChatRepository = Depends(get_chat_repo),
    redis: aioredis.Redis = Depends(get_redis),
    agent_svc: AgentService = Depends(get_agent_service),
) -> ChatService:
    return ChatService(repo, redis, agent_svc)

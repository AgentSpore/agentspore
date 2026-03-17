"""ChatRepository — data access layer for agent_messages, agent_dms."""

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db


class ChatRepository:
    """All database operations for chat messages and DMs."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Auth helpers ────────────────────────────────────────────────

    async def get_agent_by_api_key_hash(self, key_hash: str) -> dict | None:
        result = await self.db.execute(
            text("SELECT id, name, specialization FROM agents WHERE api_key_hash = :h AND is_active = TRUE"),
            {"h": key_hash},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_agent_id_by_handle(self, handle: str) -> str | None:
        result = await self.db.execute(
            text("SELECT id FROM agents WHERE handle = :handle AND is_active = TRUE"),
            {"handle": handle},
        )
        row = result.mappings().first()
        return str(row["id"]) if row else None

    async def get_agent_by_handle(self, handle: str) -> dict | None:
        result = await self.db.execute(
            text("SELECT id, name FROM agents WHERE handle = :handle AND is_active = TRUE"),
            {"handle": handle},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    # ── Messages ────────────────────────────────────────────────────

    async def get_recent_messages(self, limit: int = 100, before: str | None = None) -> list[dict]:
        params: dict = {"limit": limit}
        before_clause = ""
        if before:
            before_clause = "WHERE m.created_at < (SELECT created_at FROM agent_messages WHERE id = :before_id)"
            params["before_id"] = before
        result = await self.db.execute(
            text(f"""
                SELECT m.id, m.agent_id, m.content, m.message_type, m.created_at,
                       m.sender_type, m.human_name,
                       a.name AS agent_name, a.specialization
                FROM agent_messages m
                LEFT JOIN agents a ON a.id = m.agent_id
                {before_clause}
                ORDER BY m.created_at DESC
                LIMIT :limit
            """),
            params,
        )
        messages = []
        for row in result.mappings():
            sender_type = row["sender_type"] or "agent"
            if sender_type in ("human", "user"):
                messages.append({
                    "id": str(row["id"]),
                    "agent_id": None,
                    "agent_name": row["human_name"],
                    "specialization": sender_type,
                    "content": row["content"],
                    "message_type": row["message_type"],
                    "sender_type": sender_type,
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

    async def insert_agent_message(
        self, agent_id, content: str, message_type: str, model_used: str | None,
    ) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO agent_messages (agent_id, content, message_type, model_used)
                VALUES (:agent_id, :content, :message_type, :model_used)
                RETURNING id, created_at
            """),
            {"agent_id": agent_id, "content": content, "message_type": message_type, "model_used": model_used},
        )
        return dict(result.mappings().first())

    async def insert_human_message(
        self, content: str, message_type: str, human_name: str, sender_type: str = "human",
    ) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO agent_messages (agent_id, content, message_type, sender_type, human_name)
                VALUES (NULL, :content, :message_type, :sender_type, :human_name)
                RETURNING id, created_at
            """),
            {"content": content, "message_type": message_type, "human_name": human_name, "sender_type": sender_type},
        )
        return dict(result.mappings().first())

    async def log_model_usage(self, agent_id, model: str) -> None:
        await self.db.execute(
            text("""
                INSERT INTO agent_model_usage (agent_id, model, task_type, ref_type)
                VALUES (:agent_id, :model, 'chat', 'chat_message')
            """),
            {"agent_id": agent_id, "model": model},
        )

    # ── DMs ─────────────────────────────────────────────────────────

    async def get_dm_by_id(self, dm_id, agent_id) -> dict | None:
        result = await self.db.execute(
            text("SELECT from_agent_id, to_agent_id, human_name FROM agent_dms WHERE id = :id AND to_agent_id = :my_id"),
            {"id": dm_id, "my_id": agent_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def insert_dm(self, to_agent_id, from_agent_id, content: str, human_name: str | None = None, reply_to_dm_id: str | None = None, is_read: bool = False) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO agent_dms (to_agent_id, from_agent_id, human_name, content, reply_to_dm_id, is_read)
                VALUES (:to_id, :from_id, :name, :content, :reply_to, :is_read)
                RETURNING id, created_at
            """),
            {"to_id": to_agent_id, "from_id": from_agent_id, "name": human_name, "content": content, "reply_to": reply_to_dm_id, "is_read": is_read},
        )
        return dict(result.mappings().first())

    async def get_dm_history(self, agent_id, limit: int = 50, before: str | None = None) -> list[dict]:
        params: dict = {"agent_id": agent_id, "limit": limit}
        before_clause = ""
        if before:
            before_clause = "AND d.created_at < (SELECT created_at FROM agent_dms WHERE id = :before_id)"
            params["before_id"] = before
        result = await self.db.execute(
            text(f"""
                SELECT d.id, d.content, d.from_agent_id, d.human_name, d.is_read, d.created_at,
                       d.reply_to_dm_id,
                       a.name as from_agent_name, a.handle as from_agent_handle,
                       r.content as reply_to_content
                FROM agent_dms d
                LEFT JOIN agents a ON a.id = d.from_agent_id
                LEFT JOIN agent_dms r ON r.id = d.reply_to_dm_id
                WHERE d.to_agent_id = :agent_id {before_clause}
                ORDER BY d.created_at DESC
                LIMIT :limit
            """),
            params,
        )
        messages = []
        for dm in result.mappings():
            msg = {
                "id": str(dm["id"]),
                "from_name": dm["from_agent_name"] or dm["human_name"] or "anonymous",
                "from_handle": dm["from_agent_handle"],
                "sender_type": "agent" if dm["from_agent_id"] else "human",
                "content": dm["content"],
                "is_read": dm["is_read"],
                "created_at": str(dm["created_at"]),
            }
            if dm.get("reply_to_dm_id"):
                msg["reply_to"] = {
                    "id": str(dm["reply_to_dm_id"]),
                    "content": dm.get("reply_to_content", ""),
                }
            messages.append(msg)
        return messages


    # ── Project Chat ───────────────────────────────────────────────

    async def insert_project_message(
        self, project_id, agent_id, content: str, message_type: str,
        sender_type: str, human_name: str | None = None, reply_to_id: str | None = None,
    ) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO project_messages (project_id, agent_id, sender_type, human_name, content, message_type, reply_to_id)
                VALUES (:project_id, :agent_id, :sender_type, :human_name, :content, :message_type, :reply_to_id)
                RETURNING id, created_at
            """),
            {
                "project_id": project_id, "agent_id": agent_id, "sender_type": sender_type,
                "human_name": human_name, "content": content, "message_type": message_type,
                "reply_to_id": reply_to_id,
            },
        )
        return dict(result.mappings().first())

    async def get_project_messages(self, project_id, limit: int = 50, before: str | None = None) -> list[dict]:
        params: dict = {"project_id": project_id, "limit": limit}
        before_clause = ""
        if before:
            before_clause = "AND m.created_at < (SELECT created_at FROM project_messages WHERE id = :before_id)"
            params["before_id"] = before
        result = await self.db.execute(
            text(f"""
                SELECT m.id, m.content, m.message_type, m.sender_type, m.human_name,
                       m.agent_id, m.created_at, m.reply_to_id,
                       a.name as agent_name, a.handle as agent_handle,
                       r.content as reply_to_content
                FROM project_messages m
                LEFT JOIN agents a ON a.id = m.agent_id
                LEFT JOIN project_messages r ON r.id = m.reply_to_id
                WHERE m.project_id = :project_id {before_clause}
                ORDER BY m.created_at DESC
                LIMIT :limit
            """),
            params,
        )
        messages = []
        for row in result.mappings():
            msg = {
                "id": str(row["id"]),
                "sender_name": row["agent_name"] or row["human_name"] or "anonymous",
                "sender_handle": row["agent_handle"],
                "sender_type": row["sender_type"],
                "content": row["content"],
                "message_type": row["message_type"],
                "created_at": str(row["created_at"]),
            }
            if row.get("reply_to_id"):
                msg["reply_to"] = {
                    "id": str(row["reply_to_id"]),
                    "content": row.get("reply_to_content", ""),
                }
            messages.append(msg)
        return messages


def get_chat_repo(db: AsyncSession = Depends(get_db)) -> ChatRepository:
    return ChatRepository(db)

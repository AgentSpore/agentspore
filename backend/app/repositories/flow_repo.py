"""FlowRepository — data access layer for flows, flow_steps, flow_step_messages."""

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db


class FlowRepository:
    """All database operations for the Agent Flows feature."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Flows ──────────────────────────────────────────────────────────

    async def create_flow(self, user_id, title: str, description: str | None = None) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO flows (user_id, title, description)
                VALUES (:user_id, :title, :desc)
                RETURNING id, status, created_at
            """),
            {"user_id": str(user_id), "title": title, "desc": description},
        )
        return dict(result.mappings().first())

    async def get_flow_by_id(self, flow_id: str) -> dict | None:
        result = await self.db.execute(
            text("""
                SELECT f.*, u.name AS user_name
                FROM flows f
                JOIN users u ON u.id = f.user_id
                WHERE f.id = :id
            """),
            {"id": flow_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def list_user_flows(self, user_id, limit: int = 50) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT f.id, f.title, f.description, f.status,
                       f.total_price_tokens, f.total_platform_fee,
                       f.created_at, f.started_at, f.completed_at, f.cancelled_at,
                       (SELECT COUNT(*) FROM flow_steps WHERE flow_id = f.id) AS step_count,
                       (SELECT COUNT(*) FROM flow_steps WHERE flow_id = f.id
                        AND status IN ('approved', 'skipped')) AS completed_step_count
                FROM flows f
                WHERE f.user_id = :user_id
                ORDER BY f.created_at DESC
                LIMIT :limit
            """),
            {"user_id": str(user_id), "limit": limit},
        )
        return [dict(row) for row in result.mappings()]

    async def update_flow(self, flow_id: str, **fields) -> dict | None:
        if not fields:
            return await self.get_flow_by_id(flow_id)
        set_parts = []
        params: dict = {"id": flow_id}
        for key, val in fields.items():
            set_parts.append(f"{key} = :{key}")
            params[key] = val
        set_clause = ", ".join(set_parts)
        result = await self.db.execute(
            text(f"UPDATE flows SET {set_clause} WHERE id = :id RETURNING id, status, updated_at"),
            params,
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def update_flow_status(self, flow_id: str, status: str) -> dict | None:
        extra: dict = {}
        if status == "running":
            extra["started_at"] = text("NOW()")
        elif status == "completed":
            extra["completed_at"] = text("NOW()")
        elif status == "cancelled":
            extra["cancelled_at"] = text("NOW()")

        set_parts = ["status = :status"]
        params: dict = {"id": flow_id, "status": status}

        for col in ("started_at", "completed_at", "cancelled_at"):
            if col in extra:
                set_parts.append(f"{col} = NOW()")

        set_clause = ", ".join(set_parts)
        result = await self.db.execute(
            text(f"UPDATE flows SET {set_clause} WHERE id = :id RETURNING id, status"),
            params,
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def delete_flow(self, flow_id: str) -> bool:
        result = await self.db.execute(
            text("DELETE FROM flows WHERE id = :id AND status = 'draft'"),
            {"id": flow_id},
        )
        return result.rowcount > 0

    async def update_flow_totals(self, flow_id: str) -> None:
        await self.db.execute(
            text("""
                UPDATE flows SET
                    total_price_tokens = COALESCE((SELECT SUM(price_tokens) FROM flow_steps WHERE flow_id = :id), 0),
                    total_platform_fee = COALESCE((SELECT SUM(platform_fee) FROM flow_steps WHERE flow_id = :id), 0)
                WHERE id = :id
            """),
            {"id": flow_id},
        )

    # ── Steps ──────────────────────────────────────────────────────────

    async def create_step(
        self, flow_id: str, agent_id: str,
        title: str, instructions: str | None = None,
        depends_on: list[str] | None = None, auto_approve: bool = False,
    ) -> dict:
        next_order = await self._next_step_order(flow_id)
        dep_array = depends_on or []
        result = await self.db.execute(
            text("""
                INSERT INTO flow_steps (flow_id, agent_id, step_order, title, instructions, depends_on, auto_approve)
                VALUES (:flow_id, :agent_id, :step_order, :title, :instructions,
                        CAST(:depends_on AS text[]), :auto_approve)
                RETURNING id, step_order, status, created_at
            """),
            {
                "flow_id": flow_id, "agent_id": agent_id, "step_order": next_order,
                "title": title, "instructions": instructions,
                "depends_on": dep_array, "auto_approve": auto_approve,
            },
        )
        return dict(result.mappings().first())

    async def get_step_by_id(self, step_id: str) -> dict | None:
        result = await self.db.execute(
            text("""
                SELECT fs.*, a.name AS agent_name, a.handle AS agent_handle,
                       a.specialization, a.is_active AS agent_is_active
                FROM flow_steps fs
                JOIN agents a ON a.id = fs.agent_id
                WHERE fs.id = :id
            """),
            {"id": step_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_flow_steps(self, flow_id: str) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT fs.*, a.name AS agent_name, a.handle AS agent_handle, a.specialization
                FROM flow_steps fs
                JOIN agents a ON a.id = fs.agent_id
                WHERE fs.flow_id = :flow_id
                ORDER BY fs.step_order
            """),
            {"flow_id": flow_id},
        )
        return [dict(row) for row in result.mappings()]

    async def update_step(self, step_id: str, **fields) -> dict | None:
        if not fields:
            return await self.get_step_by_id(step_id)
        set_parts = []
        params: dict = {"id": step_id}
        for key, val in fields.items():
            if key == "depends_on":
                set_parts.append("depends_on = CAST(:depends_on AS text[])")
                params["depends_on"] = val
            else:
                set_parts.append(f"{key} = :{key}")
                params[key] = val
        set_clause = ", ".join(set_parts)
        result = await self.db.execute(
            text(f"UPDATE flow_steps SET {set_clause} WHERE id = :id RETURNING id, status, updated_at"),
            params,
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def update_step_status(
        self, step_id: str, status: str, **extra,
    ) -> dict | None:
        set_parts = ["status = :status"]
        params: dict = {"id": step_id, "status": status}

        if status == "active":
            set_parts.append("started_at = NOW()")
        elif status in ("approved", "skipped", "failed"):
            set_parts.append("completed_at = NOW()")

        for key, val in extra.items():
            set_parts.append(f"{key} = :{key}")
            params[key] = val

        set_clause = ", ".join(set_parts)
        result = await self.db.execute(
            text(f"UPDATE flow_steps SET {set_clause} WHERE id = :id RETURNING id, status"),
            params,
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def delete_step(self, step_id: str) -> bool:
        result = await self.db.execute(
            text("""
                DELETE FROM flow_steps
                WHERE id = :id
                  AND flow_id IN (SELECT id FROM flows WHERE status = 'draft')
            """),
            {"id": step_id},
        )
        return result.rowcount > 0

    async def get_agent_ready_steps(self, agent_id: str) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT fs.id, fs.flow_id, fs.title, fs.instructions, fs.input_text,
                       fs.status, fs.step_order, fs.depends_on,
                       f.title AS flow_title, f.user_id
                FROM flow_steps fs
                JOIN flows f ON f.id = fs.flow_id
                WHERE fs.agent_id = :agent_id AND fs.status IN ('ready', 'active')
                ORDER BY fs.created_at
            """),
            {"agent_id": agent_id},
        )
        return [dict(row) for row in result.mappings()]

    # ── Messages ───────────────────────────────────────────────────────

    async def insert_message(
        self, step_id: str, sender_type: str, sender_id,
        content: str, message_type: str = "text",
        file_url: str | None = None, file_name: str | None = None,
    ) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO flow_step_messages
                    (step_id, sender_type, sender_id, content, message_type, file_url, file_name)
                VALUES (:step_id, :sender_type, :sender_id, :content, :msg_type, :file_url, :file_name)
                RETURNING id, created_at
            """),
            {
                "step_id": step_id, "sender_type": sender_type,
                "sender_id": str(sender_id), "content": content,
                "msg_type": message_type, "file_url": file_url, "file_name": file_name,
            },
        )
        return dict(result.mappings().first())

    async def get_messages(self, step_id: str, limit: int = 50, before: str | None = None) -> list[dict]:
        params: dict = {"step_id": step_id, "limit": limit}
        before_clause = ""
        if before:
            before_clause = "AND m.created_at < (SELECT created_at FROM flow_step_messages WHERE id = :before_id)"
            params["before_id"] = before
        result = await self.db.execute(
            text(f"""
                SELECT m.id, m.sender_type, m.sender_id, m.content,
                       m.message_type, m.file_url, m.file_name, m.created_at,
                       CASE
                           WHEN m.sender_type = 'agent' THEN a.name
                           WHEN m.sender_type = 'user' THEN u.name
                           ELSE 'System'
                       END AS sender_name
                FROM flow_step_messages m
                LEFT JOIN agents a ON m.sender_type = 'agent' AND a.id = m.sender_id
                LEFT JOIN users u ON m.sender_type = 'user' AND u.id = m.sender_id
                WHERE m.step_id = :step_id {before_clause}
                ORDER BY m.created_at DESC
                LIMIT :limit
            """),
            params,
        )
        return [
            {
                "id": str(row["id"]),
                "sender_type": row["sender_type"],
                "sender_id": str(row["sender_id"]),
                "sender_name": row["sender_name"],
                "content": row["content"],
                "message_type": row["message_type"],
                "file_url": row["file_url"],
                "file_name": row["file_name"],
                "created_at": str(row["created_at"]),
            }
            for row in result.mappings()
        ]

    # ── Helpers ────────────────────────────────────────────────────────

    async def _next_step_order(self, flow_id: str) -> int:
        result = await self.db.execute(
            text("SELECT COALESCE(MAX(step_order), -1) + 1 AS next_order FROM flow_steps WHERE flow_id = :fid"),
            {"fid": flow_id},
        )
        return result.mappings().first()["next_order"]


def get_flow_repo(db: AsyncSession = Depends(get_db)) -> FlowRepository:
    return FlowRepository(db)

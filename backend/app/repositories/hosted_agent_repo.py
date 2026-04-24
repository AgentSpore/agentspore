"""Repository for hosted agents — CRUD, files, owner messages."""

import json

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db


UPDATABLE_FIELDS = frozenset({
    "system_prompt", "model", "budget_usd",
    "heartbeat_enabled", "heartbeat_seconds",
    "stuck_loop_detection",
})


class HostedAgentRepository:
    """Data access for hosted_agents, agent_files, owner_messages tables."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Hosted agent CRUD ──

    async def create(self, params: dict) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO hosted_agents (agent_id, owner_user_id, system_prompt, model, runtime, agent_api_key)
                VALUES (:agent_id, :owner_user_id, :system_prompt, :model, :runtime, :agent_api_key)
                RETURNING *
            """),
            params,
        )
        await self.db.commit()
        return dict(result.mappings().first())

    async def get_by_id(self, hosted_id: str, include_api_key: bool = False) -> dict | None:
        cols = "h.*" if include_api_key else (
            "h.id, h.agent_id, h.owner_user_id, h.system_prompt, h.model, h.runtime, "
            "h.status, h.memory_limit_mb, h.heartbeat_enabled, h.heartbeat_seconds, "
            "h.stuck_loop_detection, h.total_cost_usd, h.budget_usd, h.container_id, "
            "h.infra_host, h.started_at, h.stopped_at, h.created_at, h.updated_at"
        )
        result = await self.db.execute(
            text(f"""
                SELECT {cols}, a.name AS agent_name, a.handle AS agent_handle
                FROM hosted_agents h
                JOIN agents a ON a.id = h.agent_id
                WHERE h.id = :id
            """),
            {"id": hosted_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_by_agent_id(self, agent_id: str) -> dict | None:
        result = await self.db.execute(
            text("""
                SELECT h.*, a.name AS agent_name, a.handle AS agent_handle
                FROM hosted_agents h
                JOIN agents a ON a.id = h.agent_id
                WHERE h.agent_id = :agent_id
            """),
            {"agent_id": agent_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def count_by_owner(self, owner_user_id: str) -> int:
        result = await self.db.execute(
            text("SELECT COUNT(*) FROM hosted_agents WHERE owner_user_id = :uid"),
            {"uid": owner_user_id},
        )
        return result.scalar() or 0

    async def list_by_owner(self, owner_user_id: str) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT h.id, h.agent_id, h.status, h.model, h.runtime,
                       h.total_cost_usd, h.created_at, h.forked_from_agent_name,
                       a.name AS agent_name, a.handle AS agent_handle
                FROM hosted_agents h
                JOIN agents a ON a.id = h.agent_id
                WHERE h.owner_user_id = :owner_user_id
                ORDER BY h.created_at DESC
            """),
            {"owner_user_id": owner_user_id},
        )
        return [dict(r) for r in result.mappings()]

    async def list_running(self) -> list[dict]:
        result = await self.db.execute(
            text("SELECT * FROM hosted_agents WHERE status = 'running'"),
        )
        return [dict(r) for r in result.mappings()]

    async def update(self, hosted_id: str, updates: dict) -> dict | None:
        safe = {k: v for k, v in updates.items() if k in UPDATABLE_FIELDS}
        if not safe:
            return await self.get_by_id(hosted_id)
        set_clauses = ", ".join(f"{k} = :{k}" for k in safe)
        safe["id"] = hosted_id
        result = await self.db.execute(
            text(f"""
                UPDATE hosted_agents SET {set_clauses}, updated_at = now()
                WHERE id = :id RETURNING *
            """),
            safe,
        )
        await self.db.commit()
        row = result.mappings().first()
        return dict(row) if row else None

    async def update_status(self, hosted_id: str, status: str, **extra) -> None:
        params: dict = {"id": hosted_id, "status": status}
        extra_sql = ""
        if status == "running":
            extra_sql = ", started_at = now()"
        elif status == "stopped":
            extra_sql = ", stopped_at = now()"
        if "container_id" in extra:
            params["container_id"] = extra["container_id"]
            extra_sql += ", container_id = :container_id"
        await self.db.execute(
            text(f"UPDATE hosted_agents SET status = :status{extra_sql}, updated_at = now() WHERE id = :id"),
            params,
        )
        await self.db.commit()

    async def delete(self, hosted_id: str) -> None:
        await self.db.execute(text("DELETE FROM hosted_agents WHERE id = :id"), {"id": hosted_id})
        await self.db.commit()

    # ── Session history (short-term memory) ──

    async def save_session_history(self, hosted_id: str, history: list) -> None:
        await self.db.execute(
            text("UPDATE hosted_agents SET session_history = :history WHERE id = :id"),
            {"id": hosted_id, "history": json.dumps(history)},
        )
        await self.db.commit()

    async def get_session_history(self, hosted_id: str) -> list:
        result = await self.db.execute(
            text("SELECT session_history FROM hosted_agents WHERE id = :id"),
            {"id": hosted_id},
        )
        row = result.mappings().first()
        if not row or not row["session_history"]:
            return []
        raw = row["session_history"]
        return raw if isinstance(raw, list) else json.loads(raw)

    # ── Files ──

    async def upsert_file(self, hosted_id: str, file_path: str, content: str, file_type: str = "text") -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO agent_files (hosted_agent_id, file_path, content, file_type, size_bytes)
                VALUES (:hosted_agent_id, :file_path, :content, :file_type, :size_bytes)
                ON CONFLICT (hosted_agent_id, file_path) DO UPDATE
                SET content = :content, file_type = :file_type, size_bytes = :size_bytes, updated_at = now()
                RETURNING *
            """),
            {
                "hosted_agent_id": hosted_id,
                "file_path": file_path,
                "content": content,
                "file_type": file_type,
                "size_bytes": len(content.encode("utf-8")),
            },
        )
        await self.db.commit()
        return dict(result.mappings().first())

    async def get_file(self, hosted_id: str, file_path: str) -> dict | None:
        result = await self.db.execute(
            text("SELECT * FROM agent_files WHERE hosted_agent_id = :hid AND file_path = :fp"),
            {"hid": hosted_id, "fp": file_path},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def list_files(self, hosted_id: str) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT id, file_path, file_type, size_bytes, updated_at, created_at
                FROM agent_files WHERE hosted_agent_id = :hid ORDER BY file_path
            """),
            {"hid": hosted_id},
        )
        return [dict(r) for r in result.mappings()]

    async def get_public_by_id(self, hosted_id: str) -> dict | None:
        """Get a public hosted agent (no owner check)."""
        result = await self.db.execute(
            text("""
                SELECT h.id, h.agent_id, h.owner_user_id, h.system_prompt, h.model,
                       h.forked_from_hosted_id, h.forked_from_agent_name, h.is_public,
                       a.name AS agent_name, a.handle AS agent_handle,
                       a.specialization, a.skills, a.description, a.fork_count
                FROM hosted_agents h
                JOIN agents a ON a.id = h.agent_id
                WHERE h.id = :id AND h.is_public = TRUE
            """),
            {"id": hosted_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_public_by_agent_id(self, agent_id: str) -> dict | None:
        """Get a public hosted agent by platform agent_id."""
        result = await self.db.execute(
            text("""
                SELECT h.id, h.agent_id, h.owner_user_id, h.system_prompt, h.model,
                       h.forked_from_hosted_id, h.forked_from_agent_name, h.is_public,
                       a.name AS agent_name, a.handle AS agent_handle,
                       a.specialization, a.skills, a.description, a.fork_count
                FROM hosted_agents h
                JOIN agents a ON a.id = h.agent_id
                WHERE h.agent_id = :agent_id AND h.is_public = TRUE
            """),
            {"agent_id": agent_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def list_forkable(self, limit: int = 50) -> list[dict]:
        """List public hosted agents available for forking."""
        result = await self.db.execute(
            text("""
                SELECT h.id, h.agent_id, h.model, h.forked_from_agent_name,
                       a.name AS agent_name, a.handle AS agent_handle,
                       a.specialization, a.skills, a.description, a.fork_count
                FROM hosted_agents h
                JOIN agents a ON a.id = h.agent_id
                WHERE h.is_public = TRUE
                ORDER BY a.fork_count DESC, h.created_at DESC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        return [dict(r) for r in result.mappings()]

    async def increment_fork_count(self, agent_id: str) -> None:
        await self.db.execute(
            text("UPDATE agents SET fork_count = fork_count + 1 WHERE id = :id"),
            {"id": agent_id},
        )
        await self.db.commit()

    async def list_files_with_content(self, hosted_id: str) -> list[dict]:
        """List all files with content for cloning."""
        result = await self.db.execute(
            text("""
                SELECT file_path, file_type, content, size_bytes
                FROM agent_files WHERE hosted_agent_id = :hid ORDER BY file_path
            """),
            {"hid": hosted_id},
        )
        return [dict(r) for r in result.mappings()]

    async def delete_file(self, hosted_id: str, file_path: str) -> bool:
        result = await self.db.execute(
            text("DELETE FROM agent_files WHERE hosted_agent_id = :hid AND file_path = :fp"),
            {"hid": hosted_id, "fp": file_path},
        )
        await self.db.commit()
        return result.rowcount > 0

    # ── Owner messages ──

    async def add_owner_message(
        self, hosted_id: str, sender_type: str, content: str,
        tool_calls: list | None = None, thinking: str | None = None,
    ) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO owner_messages (hosted_agent_id, sender_type, content, tool_calls, thinking)
                VALUES (:hid, :sender_type, :content, :tool_calls, :thinking) RETURNING *
            """),
            {
                "hid": hosted_id, "sender_type": sender_type, "content": content,
                "tool_calls": json.dumps(tool_calls) if tool_calls else None,
                "thinking": thinking or None,
            },
        )
        await self.db.commit()
        return dict(result.mappings().first())

    async def get_owner_messages(self, hosted_id: str, limit: int = 50) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT * FROM owner_messages
                WHERE hosted_agent_id = :hid AND is_deleted = FALSE
                ORDER BY created_at DESC LIMIT :limit
            """),
            {"hid": hosted_id, "limit": limit},
        )
        return [dict(r) for r in result.mappings()]

    async def edit_owner_message(self, message_id: str, content: str) -> dict | None:
        result = await self.db.execute(
            text("""
                UPDATE owner_messages SET content = :content, edited_at = now()
                WHERE id = :id RETURNING *
            """),
            {"id": message_id, "content": content},
        )
        await self.db.commit()
        row = result.mappings().first()
        return dict(row) if row else None

    async def delete_owner_message(self, message_id: str) -> bool:
        result = await self.db.execute(
            text("UPDATE owner_messages SET is_deleted = TRUE, content = '[deleted]' WHERE id = :id"),
            {"id": message_id},
        )
        await self.db.commit()
        return result.rowcount > 0


    # ── Cron tasks ──

    async def create_cron_task(self, params: dict) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO agent_cron_tasks
                    (hosted_agent_id, name, cron_expression, task_prompt, enabled, auto_start, max_runs, next_run_at)
                VALUES
                    (:hosted_agent_id, :name, :cron_expression, :task_prompt, :enabled, :auto_start, :max_runs, :next_run_at)
                RETURNING *
            """),
            params,
        )
        await self.db.commit()
        return dict(result.mappings().first())

    async def list_cron_tasks(self, hosted_agent_id: str) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT * FROM agent_cron_tasks
                WHERE hosted_agent_id = :hid ORDER BY created_at
            """),
            {"hid": hosted_agent_id},
        )
        return [dict(r) for r in result.mappings()]

    async def get_cron_task(self, task_id: str) -> dict | None:
        result = await self.db.execute(
            text("SELECT * FROM agent_cron_tasks WHERE id = :id"),
            {"id": task_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def update_cron_task(self, task_id: str, updates: dict) -> dict | None:
        allowed = {"name", "cron_expression", "task_prompt", "enabled", "auto_start", "max_runs", "next_run_at"}
        safe = {k: v for k, v in updates.items() if k in allowed}
        if not safe:
            return await self.get_cron_task(task_id)
        set_clauses = ", ".join(f"{k} = :{k}" for k in safe)
        safe["id"] = task_id
        result = await self.db.execute(
            text(f"UPDATE agent_cron_tasks SET {set_clauses}, updated_at = now() WHERE id = :id RETURNING *"),
            safe,
        )
        await self.db.commit()
        row = result.mappings().first()
        return dict(row) if row else None

    async def delete_cron_task(self, task_id: str) -> bool:
        result = await self.db.execute(
            text("DELETE FROM agent_cron_tasks WHERE id = :id"), {"id": task_id},
        )
        await self.db.commit()
        return result.rowcount > 0

    async def get_due_cron_tasks(self) -> list[dict]:
        """Atomically claim due tasks. Safe across concurrent workers.

        Uses FOR UPDATE SKIP LOCKED to prevent multiple workers from
        grabbing the same row. Claimed rows are leased for 10 minutes
        by setting next_run_at to now() + 10min; mark_cron_run overrides
        this with the correct next_run after execution. If a worker
        crashes mid-task, the lease expires and the task re-runs.
        """
        result = await self.db.execute(
            text("""
                WITH claimed AS (
                    UPDATE agent_cron_tasks
                    SET next_run_at = now() + interval '10 minutes',
                        updated_at = now()
                    WHERE id IN (
                        SELECT id FROM agent_cron_tasks
                        WHERE enabled = TRUE
                          AND next_run_at IS NOT NULL
                          AND next_run_at <= now()
                          AND (max_runs IS NULL OR run_count < max_runs)
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING *
                )
                SELECT c.*, h.status AS agent_status, h.owner_user_id,
                       a.name AS agent_name
                FROM claimed c
                JOIN hosted_agents h ON h.id = c.hosted_agent_id
                JOIN agents a ON a.id = h.agent_id
            """),
        )
        rows = [dict(r) for r in result.mappings()]
        await self.db.commit()
        return rows

    async def mark_cron_run(self, task_id: str, next_run_at, error: str | None = None) -> None:
        await self.db.execute(
            text("""
                UPDATE agent_cron_tasks
                SET run_count = run_count + 1, last_run_at = now(),
                    next_run_at = :next_run, last_error = :error, updated_at = now()
                WHERE id = :id
            """),
            {"id": task_id, "next_run": next_run_at, "error": error},
        )
        await self.db.commit()


def get_hosted_agent_repo(db: AsyncSession = Depends(get_db)) -> HostedAgentRepository:
    return HostedAgentRepository(db)

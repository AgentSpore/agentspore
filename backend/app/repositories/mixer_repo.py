"""MixerRepository — data access layer for mixer sessions, fragments, chunks, messages, audit."""

from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class MixerRepository:
    """All database operations for the Privacy Mixer feature."""

    # ── Sessions ────────────────────────────────────────────────────────

    async def create_session(
        self, db: AsyncSession, user_id: str, title: str,
        description: str | None, original_text: str,
        passphrase_salt: bytes, passphrase_hash: str,
        encryption_iv: bytes, fragment_ttl_hours: int,
    ) -> dict:
        result = await db.execute(
            text("""
                INSERT INTO mixer_sessions
                    (user_id, title, description, original_text,
                     passphrase_salt, passphrase_hash, encryption_iv, fragment_ttl_hours)
                VALUES (:user_id, :title, :desc, :original_text,
                        :salt, :hash, :iv, :ttl)
                RETURNING id, status, created_at
            """),
            {
                "user_id": user_id, "title": title, "desc": description,
                "original_text": original_text, "salt": passphrase_salt,
                "hash": passphrase_hash, "iv": encryption_iv,
                "ttl": fragment_ttl_hours,
            },
        )
        return dict(result.mappings().first())

    async def get_session_by_id(self, db: AsyncSession, session_id: str) -> dict | None:
        result = await db.execute(
            text("""
                SELECT ms.*, u.name AS user_name,
                       (SELECT COUNT(*) FROM mixer_fragments WHERE session_id = ms.id) AS fragment_count,
                       (SELECT COUNT(*) FROM mixer_chunks WHERE session_id = ms.id) AS chunk_count,
                       (SELECT COUNT(*) FROM mixer_chunks WHERE session_id = ms.id
                        AND status IN ('approved')) AS completed_chunk_count
                FROM mixer_sessions ms
                JOIN users u ON u.id = ms.user_id
                WHERE ms.id = :id
            """),
            {"id": session_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def list_user_sessions(self, db: AsyncSession, user_id: str, limit: int = 50) -> list[dict]:
        result = await db.execute(
            text("""
                SELECT ms.id, ms.title, ms.description, ms.status,
                       ms.fragment_ttl_hours, ms.created_at, ms.started_at,
                       ms.completed_at, ms.cancelled_at,
                       (SELECT COUNT(*) FROM mixer_fragments WHERE session_id = ms.id) AS fragment_count,
                       (SELECT COUNT(*) FROM mixer_chunks WHERE session_id = ms.id) AS chunk_count,
                       (SELECT COUNT(*) FROM mixer_chunks WHERE session_id = ms.id
                        AND status IN ('approved')) AS completed_chunk_count
                FROM mixer_sessions ms
                WHERE ms.user_id = :user_id
                ORDER BY ms.created_at DESC
                LIMIT :limit
            """),
            {"user_id": user_id, "limit": limit},
        )
        return [dict(row) for row in result.mappings()]

    async def update_session(self, db: AsyncSession, session_id: str, **fields) -> dict | None:
        if not fields:
            return await self.get_session_by_id(db, session_id)
        set_parts = []
        params: dict = {"id": session_id}
        for key, val in fields.items():
            set_parts.append(f"{key} = :{key}")
            params[key] = val
        set_clause = ", ".join(set_parts)
        result = await db.execute(
            text(f"UPDATE mixer_sessions SET {set_clause} WHERE id = :id RETURNING id, status, updated_at"),
            params,
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def update_session_status(self, db: AsyncSession, session_id: str, status: str) -> dict | None:
        set_parts = ["status = :status"]
        params: dict = {"id": session_id, "status": status}

        if status == "running":
            set_parts.append("started_at = NOW()")
            set_parts.append("expires_at = NOW() + (fragment_ttl_hours || ' hours')::interval")
        elif status == "completed":
            set_parts.append("completed_at = NOW()")
        elif status == "cancelled":
            set_parts.append("cancelled_at = NOW()")

        set_clause = ", ".join(set_parts)
        result = await db.execute(
            text(f"UPDATE mixer_sessions SET {set_clause} WHERE id = :id RETURNING id, status"),
            params,
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def delete_session(self, db: AsyncSession, session_id: str) -> bool:
        result = await db.execute(
            text("DELETE FROM mixer_sessions WHERE id = :id AND status = 'draft'"),
            {"id": session_id},
        )
        return result.rowcount > 0

    # ── Fragments ───────────────────────────────────────────────────────

    async def create_fragment(
        self, db: AsyncSession, session_id: str,
        placeholder: str, encrypted_value: bytes, category: str | None = None,
    ) -> dict:
        result = await db.execute(
            text("""
                INSERT INTO mixer_fragments (session_id, placeholder, encrypted_value, category)
                VALUES (:session_id, :placeholder, :encrypted_value, :category)
                RETURNING id, placeholder, category, created_at
            """),
            {
                "session_id": session_id, "placeholder": placeholder,
                "encrypted_value": encrypted_value, "category": category,
            },
        )
        return dict(result.mappings().first())

    async def get_fragments(self, db: AsyncSession, session_id: str) -> list[dict]:
        result = await db.execute(
            text("""
                SELECT id, session_id, placeholder, encrypted_value, category, created_at
                FROM mixer_fragments
                WHERE session_id = :session_id
                ORDER BY created_at
            """),
            {"session_id": session_id},
        )
        return [dict(row) for row in result.mappings()]

    async def get_fragment_placeholders(self, db: AsyncSession, session_id: str) -> list[dict]:
        """Return placeholders and categories only (no encrypted values)."""
        result = await db.execute(
            text("""
                SELECT placeholder, category
                FROM mixer_fragments
                WHERE session_id = :session_id
                ORDER BY created_at
            """),
            {"session_id": session_id},
        )
        return [dict(row) for row in result.mappings()]

    async def delete_fragments(self, db: AsyncSession, session_id: str) -> int:
        result = await db.execute(
            text("DELETE FROM mixer_fragments WHERE session_id = :session_id"),
            {"session_id": session_id},
        )
        return result.rowcount

    # ── Chunks ──────────────────────────────────────────────────────────

    async def create_chunk(
        self, db: AsyncSession, session_id: str, agent_id: str,
        title: str, instructions: str | None = None,
    ) -> dict:
        next_order = await self._next_chunk_order(db, session_id)
        result = await db.execute(
            text("""
                INSERT INTO mixer_chunks (session_id, agent_id, chunk_order, title, instructions)
                VALUES (:session_id, :agent_id, :chunk_order, :title, :instructions)
                RETURNING id, chunk_order, status, created_at
            """),
            {
                "session_id": session_id, "agent_id": agent_id,
                "chunk_order": next_order, "title": title,
                "instructions": instructions,
            },
        )
        return dict(result.mappings().first())

    async def get_chunk_by_id(self, db: AsyncSession, chunk_id: str) -> dict | None:
        result = await db.execute(
            text("""
                SELECT mc.*, a.name AS agent_name, a.handle AS agent_handle,
                       a.specialization, a.model_provider
                FROM mixer_chunks mc
                JOIN agents a ON a.id = mc.agent_id
                WHERE mc.id = :id
            """),
            {"id": chunk_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_session_chunks(self, db: AsyncSession, session_id: str) -> list[dict]:
        result = await db.execute(
            text("""
                SELECT mc.*, a.name AS agent_name, a.handle AS agent_handle,
                       a.specialization, a.model_provider
                FROM mixer_chunks mc
                JOIN agents a ON a.id = mc.agent_id
                WHERE mc.session_id = :session_id
                ORDER BY mc.chunk_order
            """),
            {"session_id": session_id},
        )
        return [dict(row) for row in result.mappings()]

    async def update_chunk(self, db: AsyncSession, chunk_id: str, **fields) -> dict | None:
        if not fields:
            return await self.get_chunk_by_id(db, chunk_id)
        set_parts = []
        params: dict = {"id": chunk_id}
        for key, val in fields.items():
            set_parts.append(f"{key} = :{key}")
            params[key] = val
        set_clause = ", ".join(set_parts)
        result = await db.execute(
            text(f"UPDATE mixer_chunks SET {set_clause} WHERE id = :id RETURNING id, status, updated_at"),
            params,
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def update_chunk_status(self, db: AsyncSession, chunk_id: str, status: str, **extra) -> dict | None:
        set_parts = ["status = :status"]
        params: dict = {"id": chunk_id, "status": status}

        if status == "active":
            set_parts.append("started_at = NOW()")
        elif status in ("approved", "failed"):
            set_parts.append("completed_at = NOW()")

        for key, val in extra.items():
            set_parts.append(f"{key} = :{key}")
            params[key] = val

        set_clause = ", ".join(set_parts)
        result = await db.execute(
            text(f"UPDATE mixer_chunks SET {set_clause} WHERE id = :id RETURNING id, status"),
            params,
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def delete_chunk(self, db: AsyncSession, chunk_id: str) -> bool:
        result = await db.execute(
            text("""
                DELETE FROM mixer_chunks
                WHERE id = :id
                  AND session_id IN (SELECT id FROM mixer_sessions WHERE status = 'draft')
            """),
            {"id": chunk_id},
        )
        return result.rowcount > 0

    async def get_agent_ready_chunks(self, db: AsyncSession, agent_id: str) -> list[dict]:
        result = await db.execute(
            text("""
                SELECT mc.id, mc.session_id, mc.title, mc.instructions,
                       mc.status, mc.chunk_order,
                       ms.title AS session_title, ms.user_id
                FROM mixer_chunks mc
                JOIN mixer_sessions ms ON ms.id = mc.session_id
                WHERE mc.agent_id = :agent_id AND mc.status IN ('ready', 'active')
                ORDER BY mc.created_at
            """),
            {"agent_id": agent_id},
        )
        return [dict(row) for row in result.mappings()]

    # ── Messages ────────────────────────────────────────────────────────

    async def insert_message(
        self, db: AsyncSession, chunk_id: str, sender_type: str, sender_id: str,
        content: str, message_type: str = "text",
    ) -> dict:
        result = await db.execute(
            text("""
                INSERT INTO mixer_chunk_messages
                    (chunk_id, sender_type, sender_id, content, message_type)
                VALUES (:chunk_id, :sender_type, :sender_id, :content, :msg_type)
                RETURNING id, created_at
            """),
            {
                "chunk_id": chunk_id, "sender_type": sender_type,
                "sender_id": str(sender_id), "content": content,
                "msg_type": message_type,
            },
        )
        return dict(result.mappings().first())

    async def get_messages(self, db: AsyncSession, chunk_id: str, limit: int = 200) -> list[dict]:
        result = await db.execute(
            text("""
                SELECT m.id, m.sender_type, m.sender_id, m.content,
                       m.message_type, m.created_at,
                       CASE
                           WHEN m.sender_type = 'agent' THEN a.name
                           WHEN m.sender_type = 'user' THEN u.name
                           ELSE 'System'
                       END AS sender_name
                FROM mixer_chunk_messages m
                LEFT JOIN agents a ON m.sender_type = 'agent' AND a.id = m.sender_id
                LEFT JOIN users u ON m.sender_type = 'user' AND u.id = m.sender_id
                WHERE m.chunk_id = :chunk_id
                ORDER BY m.created_at ASC
                LIMIT :limit
            """),
            {"chunk_id": chunk_id, "limit": limit},
        )
        return [
            {
                "id": str(row["id"]),
                "sender_type": row["sender_type"],
                "sender_id": str(row["sender_id"]),
                "sender_name": row["sender_name"],
                "content": row["content"],
                "message_type": row["message_type"],
                "created_at": str(row["created_at"]),
            }
            for row in result.mappings()
        ]

    # ── Audit Log ───────────────────────────────────────────────────────

    async def log_audit(
        self, db: AsyncSession, session_id: str,
        actor_type: str, actor_id: str, action: str,
        target_type: str | None = None, target_id: str | None = None,
        details: dict | None = None, ip_address: str | None = None,
    ) -> dict:
        import json
        result = await db.execute(
            text("""
                INSERT INTO mixer_audit_log
                    (session_id, actor_type, actor_id, action, target_type, target_id, details, ip_address)
                VALUES (:session_id, :actor_type, :actor_id, :action, :target_type, :target_id,
                        CAST(:details AS jsonb), :ip_address)
                RETURNING id, created_at
            """),
            {
                "session_id": session_id, "actor_type": actor_type,
                "actor_id": actor_id, "action": action,
                "target_type": target_type, "target_id": target_id,
                "details": json.dumps(details or {}),
                "ip_address": ip_address,
            },
        )
        return dict(result.mappings().first())

    async def get_audit_log(self, db: AsyncSession, session_id: str, limit: int = 500) -> list[dict]:
        result = await db.execute(
            text("""
                SELECT id, actor_type, actor_id, action, target_type, target_id,
                       details, ip_address, created_at
                FROM mixer_audit_log
                WHERE session_id = :session_id
                ORDER BY created_at ASC
                LIMIT :limit
            """),
            {"session_id": session_id, "limit": limit},
        )
        return [
            {
                "id": str(row["id"]),
                "actor_type": row["actor_type"],
                "actor_id": str(row["actor_id"]),
                "action": row["action"],
                "target_type": row["target_type"],
                "target_id": str(row["target_id"]) if row["target_id"] else None,
                "details": row["details"],
                "ip_address": row["ip_address"],
                "created_at": str(row["created_at"]),
            }
            for row in result.mappings()
        ]

    # ── Cleanup ─────────────────────────────────────────────────────────

    async def get_expired_sessions(self, db: AsyncSession) -> list[dict]:
        result = await db.execute(
            text("""
                SELECT id FROM mixer_sessions
                WHERE expires_at IS NOT NULL AND expires_at < NOW()
                  AND status NOT IN ('completed', 'cancelled')
            """),
        )
        return [dict(row) for row in result.mappings()]

    async def cleanup_expired_fragments(self, db: AsyncSession, session_id: str) -> int:
        result = await db.execute(
            text("DELETE FROM mixer_fragments WHERE session_id = :session_id"),
            {"session_id": session_id},
        )
        # Also clear assembled_output
        await db.execute(
            text("UPDATE mixer_sessions SET assembled_output = NULL WHERE id = :id"),
            {"id": session_id},
        )
        return result.rowcount

    # ── Helpers ─────────────────────────────────────────────────────────

    async def _next_chunk_order(self, db: AsyncSession, session_id: str) -> int:
        result = await db.execute(
            text("SELECT COALESCE(MAX(chunk_order), -1) + 1 AS next_order FROM mixer_chunks WHERE session_id = :sid"),
            {"sid": session_id},
        )
        return result.mappings().first()["next_order"]


@lru_cache
def get_mixer_repo() -> MixerRepository:
    return MixerRepository()

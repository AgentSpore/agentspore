"""BlogRepository — data access layer for blog_posts and blog_reactions."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class BlogRepository:
    """All database operations for the Agent Blog feature."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Posts ──────────────────────────────────────────────────────────

    async def create_post(self, agent_id: UUID, title: str, content: str) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO blog_posts (agent_id, title, content)
                VALUES (:aid, :title, :content)
                RETURNING id, agent_id, title, content, created_at
            """),
            {"aid": str(agent_id), "title": title, "content": content},
        )
        return dict(result.mappings().first())

    async def get_post_by_id(self, post_id: UUID) -> dict | None:
        result = await self.db.execute(
            text("""
                SELECT bp.id, bp.agent_id, bp.title, bp.content, bp.created_at, bp.updated_at,
                       a.name AS agent_name, a.handle AS agent_handle
                FROM blog_posts bp
                JOIN agents a ON a.id = bp.agent_id
                WHERE bp.id = :id AND bp.is_published = TRUE
            """),
            {"id": str(post_id)},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def list_posts(self, limit: int, offset: int) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT bp.id, bp.agent_id, bp.title, bp.content, bp.created_at,
                       a.name AS agent_name, a.handle AS agent_handle
                FROM blog_posts bp
                JOIN agents a ON a.id = bp.agent_id
                WHERE bp.is_published = TRUE
                ORDER BY bp.created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": limit, "offset": offset},
        )
        return [dict(r) for r in result.mappings()]

    async def count_posts(self) -> int:
        result = await self.db.execute(
            text("SELECT COUNT(*) AS cnt FROM blog_posts WHERE is_published = TRUE")
        )
        return result.mappings().first()["cnt"]

    async def list_agent_posts(self, agent_id: UUID, limit: int, offset: int) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT bp.id, bp.agent_id, bp.title, bp.content, bp.created_at,
                       a.name AS agent_name, a.handle AS agent_handle
                FROM blog_posts bp
                JOIN agents a ON a.id = bp.agent_id
                WHERE bp.agent_id = :aid AND bp.is_published = TRUE
                ORDER BY bp.created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"aid": str(agent_id), "limit": limit, "offset": offset},
        )
        return [dict(r) for r in result.mappings()]

    async def count_agent_posts(self, agent_id: UUID) -> int:
        result = await self.db.execute(
            text("SELECT COUNT(*) AS cnt FROM blog_posts WHERE agent_id = :aid AND is_published = TRUE"),
            {"aid": str(agent_id)},
        )
        return result.mappings().first()["cnt"]

    async def update_post(self, post_id: UUID, updates: dict) -> None:
        set_parts = [f"{k} = :{k}" for k in updates]
        updates["id"] = str(post_id)
        await self.db.execute(
            text(f"UPDATE blog_posts SET {', '.join(set_parts)} WHERE id = :id"),
            updates,
        )

    async def delete_post(self, post_id: UUID) -> None:
        await self.db.execute(
            text("UPDATE blog_posts SET is_published = FALSE WHERE id = :id"),
            {"id": str(post_id)},
        )

    async def get_post_owner(self, post_id: UUID) -> UUID | None:
        result = await self.db.execute(
            text("SELECT agent_id FROM blog_posts WHERE id = :id AND is_published = TRUE"),
            {"id": str(post_id)},
        )
        row = result.mappings().first()
        return row["agent_id"] if row else None

    # ── Reactions ─────────────────────────────────────────────────────

    async def add_reaction(self, post_id: UUID, reactor_type: str, reactor_id: UUID, reaction: str) -> bool:
        result = await self.db.execute(
            text("""
                INSERT INTO blog_reactions (post_id, reactor_type, reactor_id, reaction)
                VALUES (:pid, :rtype, :rid, :reaction)
                ON CONFLICT (post_id, reactor_type, reactor_id, reaction) DO NOTHING
                RETURNING id
            """),
            {"pid": str(post_id), "rtype": reactor_type, "rid": str(reactor_id), "reaction": reaction},
        )
        return result.mappings().first() is not None

    async def remove_reaction(self, post_id: UUID, reactor_type: str, reactor_id: UUID, reaction: str) -> bool:
        result = await self.db.execute(
            text("""
                DELETE FROM blog_reactions
                WHERE post_id = :pid AND reactor_type = :rtype AND reactor_id = :rid AND reaction = :reaction
                RETURNING id
            """),
            {"pid": str(post_id), "rtype": reactor_type, "rid": str(reactor_id), "reaction": reaction},
        )
        return result.mappings().first() is not None

    async def get_reaction_counts(self, post_id: UUID) -> dict:
        result = await self.db.execute(
            text("""
                SELECT reaction, COUNT(*) AS cnt
                FROM blog_reactions WHERE post_id = :pid
                GROUP BY reaction
            """),
            {"pid": str(post_id)},
        )
        counts = {r["reaction"]: r["cnt"] for r in result.mappings()}
        return {"like": counts.get("like", 0), "fire": counts.get("fire", 0),
                "insightful": counts.get("insightful", 0), "funny": counts.get("funny", 0)}

    async def get_reaction_counts_batch(self, post_ids: list[str]) -> dict[str, dict]:
        if not post_ids:
            return {}
        result = await self.db.execute(
            text("""
                SELECT post_id, reaction, COUNT(*) AS cnt
                FROM blog_reactions WHERE post_id = ANY(:pids)
                GROUP BY post_id, reaction
            """),
            {"pids": post_ids},
        )
        counts: dict[str, dict] = {}
        for r in result.mappings():
            pid = str(r["post_id"])
            if pid not in counts:
                counts[pid] = {"like": 0, "fire": 0, "insightful": 0, "funny": 0}
            counts[pid][r["reaction"]] = r["cnt"]
        return counts

    async def get_user_reactions(self, post_id: UUID, reactor_type: str, reactor_id: UUID) -> list[str]:
        result = await self.db.execute(
            text("""
                SELECT reaction FROM blog_reactions
                WHERE post_id = :pid AND reactor_type = :rtype AND reactor_id = :rid
            """),
            {"pid": str(post_id), "rtype": reactor_type, "rid": str(reactor_id)},
        )
        return [r["reaction"] for r in result.mappings()]

    async def get_user_reactions_batch(self, post_ids: list[str], reactor_type: str, reactor_id: UUID) -> dict[str, list[str]]:
        if not post_ids:
            return {}
        result = await self.db.execute(
            text("""
                SELECT post_id, reaction FROM blog_reactions
                WHERE post_id = ANY(:pids) AND reactor_type = :rtype AND reactor_id = :rid
            """),
            {"pids": post_ids, "rtype": reactor_type, "rid": str(reactor_id)},
        )
        reactions: dict[str, list[str]] = {}
        for r in result.mappings():
            pid = str(r["post_id"])
            reactions.setdefault(pid, []).append(r["reaction"])
        return reactions

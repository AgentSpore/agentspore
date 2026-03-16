"""BlogService — business logic for agent blog posts and reactions."""

from uuid import UUID

from fastapi import Depends
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.blog_repo import BlogRepository

EMPTY_REACTIONS = {"like": 0, "fire": 0, "insightful": 0, "funny": 0}


class BlogService:
    """Agent blog: posts CRUD, reactions, feed with pagination."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = BlogRepository(db)

    # ── Posts ──────────────────────────────────────────────────────────

    async def create_post(self, agent_id: UUID, title: str, content: str) -> dict:
        post = await self.repo.create_post(agent_id, title, content)
        await self.db.commit()
        return {
            "id": str(post["id"]),
            "agent_id": str(post["agent_id"]),
            "title": post["title"],
            "created_at": str(post["created_at"]),
        }

    async def get_post(self, post_id: UUID) -> dict | None:
        post = await self.repo.get_post_by_id(post_id)
        if not post:
            return None
        reactions = await self.repo.get_reaction_counts(post_id)
        return {
            "id": str(post["id"]),
            "agent_id": str(post["agent_id"]),
            "agent_name": post["agent_name"],
            "agent_handle": post["agent_handle"],
            "title": post["title"],
            "content": post["content"],
            "reactions": reactions,
            "created_at": str(post["created_at"]),
            "updated_at": str(post["updated_at"]),
        }

    async def list_posts(self, limit: int, offset: int) -> dict:
        posts = await self.repo.list_posts(limit, offset)
        total = await self.repo.count_posts()
        post_ids = [str(p["id"]) for p in posts]
        reactions = await self.repo.get_reaction_counts_batch(post_ids)

        return {
            "posts": [
                {
                    "id": str(p["id"]),
                    "agent_id": str(p["agent_id"]),
                    "agent_name": p["agent_name"],
                    "agent_handle": p["agent_handle"],
                    "title": p["title"],
                    "content": p["content"],
                    "reactions": reactions.get(str(p["id"]), EMPTY_REACTIONS),
                    "created_at": str(p["created_at"]),
                }
                for p in posts
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def list_agent_posts(self, agent_id: UUID, limit: int, offset: int) -> dict:
        posts = await self.repo.list_agent_posts(agent_id, limit, offset)
        total = await self.repo.count_agent_posts(agent_id)
        post_ids = [str(p["id"]) for p in posts]
        reactions = await self.repo.get_reaction_counts_batch(post_ids)

        return {
            "posts": [
                {
                    "id": str(p["id"]),
                    "agent_id": str(p["agent_id"]),
                    "agent_name": p["agent_name"],
                    "agent_handle": p["agent_handle"],
                    "title": p["title"],
                    "content": p["content"],
                    "reactions": reactions.get(str(p["id"]), EMPTY_REACTIONS),
                    "created_at": str(p["created_at"]),
                }
                for p in posts
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def update_post(self, post_id: UUID, agent_id: UUID, updates: dict) -> str | None:
        """Returns None on success, error string on failure."""
        owner = await self.repo.get_post_owner(post_id)
        if not owner:
            return "Post not found"
        if str(owner) != str(agent_id):
            return "Not the post author"
        await self.repo.update_post(post_id, updates)
        await self.db.commit()
        return None

    async def delete_post(self, post_id: UUID, agent_id: UUID) -> str | None:
        """Returns None on success, error string on failure."""
        owner = await self.repo.get_post_owner(post_id)
        if not owner:
            return "Post not found"
        if str(owner) != str(agent_id):
            return "Not the post author"
        await self.repo.delete_post(post_id)
        await self.db.commit()
        return None

    # ── Reactions ─────────────────────────────────────────────────────

    async def add_reaction(self, post_id: UUID, reactor_type: str, reactor_id: UUID, reaction: str) -> str | None:
        """Returns None on success, error string on failure."""
        post = await self.repo.get_post_by_id(post_id)
        if not post:
            return "Post not found"
        added = await self.repo.add_reaction(post_id, reactor_type, reactor_id, reaction)
        if not added:
            return "Reaction already exists"
        await self.db.commit()
        return None

    async def remove_reaction(self, post_id: UUID, reactor_type: str, reactor_id: UUID, reaction: str) -> str | None:
        """Returns None on success, error string on failure."""
        removed = await self.repo.remove_reaction(post_id, reactor_type, reactor_id, reaction)
        if not removed:
            return "Reaction not found"
        await self.db.commit()
        return None


    # ── Comments ────────────────────────────────────────────────────

    async def add_comment(self, post_id: UUID, author_type: str, author_id: UUID, content: str) -> dict:
        """Add a comment to a blog post. Verifies post exists first."""
        post = await self.repo.get_post_by_id(post_id)
        if not post:
            raise ValueError("Post not found")
        comment = await self.repo.insert_comment(post_id, author_type, author_id, content)
        await self.db.commit()
        logger.info("blog comment added post_id={} author={}:{}", post_id, author_type, author_id)
        return {
            "id": str(comment["id"]),
            "post_id": str(comment["post_id"]),
            "author_type": comment["author_type"],
            "author_id": str(comment["author_id"]),
            "content": comment["content"],
            "created_at": str(comment["created_at"]),
        }

    async def get_comments(self, post_id: UUID, limit: int = 100) -> list[dict]:
        """Get comments for a post with author names."""
        rows = await self.repo.get_comments(post_id, limit)
        return [
            {
                "id": str(r["id"]),
                "post_id": str(r["post_id"]),
                "author_type": r["author_type"],
                "author_id": str(r["author_id"]),
                "author_name": r["author_name"],
                "content": r["content"],
                "created_at": str(r["created_at"]),
            }
            for r in rows
        ]

    async def delete_comment(self, comment_id: UUID, author_type: str, author_id: UUID) -> str | None:
        """Delete a comment. Returns None on success, error string on failure."""
        deleted = await self.repo.delete_comment(comment_id, author_type, author_id)
        if not deleted:
            return "Comment not found or not the author"
        await self.db.commit()
        logger.info("blog comment deleted comment_id={} by {}:{}", comment_id, author_type, author_id)
        return None


def get_blog_service(db: AsyncSession = Depends(get_db)) -> BlogService:
    return BlogService(db)

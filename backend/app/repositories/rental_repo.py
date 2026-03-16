"""Rental repository — rentals, rental_messages table queries."""

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db


class RentalRepository:
    """All database operations for the Rentals feature."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_rental(self, user_id, agent_id, title: str, price: int = 0, fee: int = 0) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO rentals (user_id, agent_id, title, price_tokens, platform_fee)
                VALUES (:user_id, :agent_id, :title, :price, :fee)
                RETURNING id, status, created_at
            """),
            {"user_id": str(user_id), "agent_id": agent_id, "title": title, "price": price, "fee": fee},
        )
        return dict(result.mappings().first())

    async def get_rental_by_id(self, rental_id: str) -> dict | None:
        result = await self.db.execute(
            text("""
                SELECT r.*, a.name AS agent_name, a.handle AS agent_handle,
                       a.specialization, a.is_active AS agent_is_active,
                       u.name AS user_name
                FROM rentals r
                JOIN agents a ON a.id = r.agent_id
                JOIN users u ON u.id = r.user_id
                WHERE r.id = :id
            """),
            {"id": rental_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def list_user_rentals(self, user_id, limit: int = 50) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT r.id, r.agent_id, r.title, r.status, r.price_tokens,
                       r.rating, r.created_at, r.completed_at, r.cancelled_at,
                       a.name AS agent_name, a.handle AS agent_handle, a.specialization
                FROM rentals r
                JOIN agents a ON a.id = r.agent_id
                WHERE r.user_id = :user_id
                ORDER BY r.created_at DESC
                LIMIT :limit
            """),
            {"user_id": str(user_id), "limit": limit},
        )
        return [dict(row) for row in result.mappings()]

    async def list_agent_rentals(self, agent_id: str, status: str | None = None) -> list[dict]:
        if status:
            result = await self.db.execute(
                text("""
                    SELECT r.id, r.user_id, r.title, r.status, r.price_tokens,
                           r.created_at, u.name AS user_name
                    FROM rentals r
                    JOIN users u ON u.id = r.user_id
                    WHERE r.agent_id = :agent_id AND r.status = :status
                    ORDER BY r.created_at DESC
                """),
                {"agent_id": agent_id, "status": status},
            )
        else:
            result = await self.db.execute(
                text("""
                    SELECT r.id, r.user_id, r.title, r.status, r.price_tokens,
                           r.created_at, u.name AS user_name
                    FROM rentals r
                    JOIN users u ON u.id = r.user_id
                    WHERE r.agent_id = :agent_id
                    ORDER BY r.created_at DESC
                """),
                {"agent_id": agent_id},
            )
        return [dict(row) for row in result.mappings()]

    async def update_rental_status(self, rental_id: str, status: str, **extra) -> dict | None:
        set_parts = ["status = :status"]
        params: dict = {"id": rental_id, "status": status}

        if status == "completed":
            set_parts.append("completed_at = NOW()")
        elif status == "cancelled":
            set_parts.append("cancelled_at = NOW()")

        if "rating" in extra:
            set_parts.append("rating = :rating")
            params["rating"] = extra["rating"]
        if "review" in extra:
            set_parts.append("review = :review")
            params["review"] = extra["review"]

        set_clause = ", ".join(set_parts)
        result = await self.db.execute(
            text(f"UPDATE rentals SET {set_clause} WHERE id = :id RETURNING id, status"),
            params,
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def insert_message(
        self, rental_id: str, sender_type: str, sender_id,
        content: str, message_type: str = "text",
        file_url: str | None = None, file_name: str | None = None,
    ) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO rental_messages (rental_id, sender_type, sender_id, content, message_type, file_url, file_name)
                VALUES (:rental_id, :sender_type, :sender_id, :content, :msg_type, :file_url, :file_name)
                RETURNING id, created_at
            """),
            {
                "rental_id": rental_id, "sender_type": sender_type, "sender_id": str(sender_id),
                "content": content, "msg_type": message_type,
                "file_url": file_url, "file_name": file_name,
            },
        )
        return dict(result.mappings().first())

    async def get_messages(self, rental_id: str, limit: int = 50, before: str | None = None) -> list[dict]:
        params: dict = {"rental_id": rental_id, "limit": limit}
        before_clause = ""
        if before:
            before_clause = "AND rm.created_at < (SELECT created_at FROM rental_messages WHERE id = :before_id)"
            params["before_id"] = before
        result = await self.db.execute(
            text(f"""
                SELECT rm.id, rm.sender_type, rm.sender_id, rm.content,
                       rm.message_type, rm.file_url, rm.file_name, rm.created_at,
                       CASE
                           WHEN rm.sender_type = 'agent' THEN a.name
                           WHEN rm.sender_type = 'user' THEN u.name
                           ELSE 'System'
                       END AS sender_name
                FROM rental_messages rm
                LEFT JOIN agents a ON rm.sender_type = 'agent' AND a.id = rm.sender_id
                LEFT JOIN users u ON rm.sender_type = 'user' AND u.id = rm.sender_id
                WHERE rm.rental_id = :rental_id {before_clause}
                ORDER BY rm.created_at DESC
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

    async def count_active_rentals_for_agent(self, agent_id: str) -> int:
        result = await self.db.execute(
            text("SELECT COUNT(*) AS cnt FROM rentals WHERE agent_id = :agent_id AND status = 'active'"),
            {"agent_id": agent_id},
        )
        return result.mappings().first()["cnt"]


def get_rental_repo(db: AsyncSession = Depends(get_db)) -> RentalRepository:
    return RentalRepository(db)

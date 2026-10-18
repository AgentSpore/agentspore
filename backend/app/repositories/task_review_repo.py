"""PostgreSQL storage for opt-in acceptance and immutable transition receipts."""

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class TaskReviewRepository:
    """Bind every review operation to the caller's request transaction."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_review(self, task_id: UUID, *, lock: bool = True) -> dict | None:
        """Read presence without locking, or lock after the caller locks the task."""
        clause = " FOR UPDATE" if lock else ""
        result = await self.db.execute(
            text("SELECT * FROM task_reviews WHERE task_id = :id" + clause), {"id": task_id}
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def create(self, task_id: UUID, requester: UUID) -> None:
        """Attach acceptance to a newly inserted, locked task without legacy backfill."""
        await self.db.execute(
            text("INSERT INTO task_reviews (task_id, requester_agent_id) VALUES (:id, :requester)"),
            {"id": task_id, "requester": requester},
        )

    async def relationship(self, requester: UUID, claimant: UUID) -> str:
        """Classify ownership without treating an email-derived owner as verified."""
        result = await self.db.execute(
            text(
                "SELECT a.owner_user_id AS requester, b.owner_user_id AS claimant "
                "FROM agents a JOIN agents b ON b.id = :claimant WHERE a.id = :requester"
            ),
            {"requester": requester, "claimant": claimant},
        )
        row = result.mappings().one()
        if row["requester"] is None or row["claimant"] is None:
            return "unknown"
        return "internal" if row["requester"] == row["claimant"] else "external_unverified"

    async def update(self, review: dict) -> None:
        """Persist one versioned transition while the parent task stays locked."""
        await self.db.execute(
            text(
                "UPDATE task_reviews SET state = :state, version = :version, "
                "submission_version = :submission_version, result = :result, "
                "relationship = :relationship, updated_at = NOW() WHERE task_id = :task_id"
            ),
            review,
        )

    async def finish_task(self, task_id: UUID, status: str, result: str | None) -> None:
        """Set the final task state in the same transaction as its acceptance."""
        await self.db.execute(
            text(
                "UPDATE tasks SET status = CAST(:status AS varchar(30)), "
                "result = :result, updated_at = NOW(), "
                "completed_at = CASE WHEN CAST(:status AS varchar(30)) = 'completed' "
                "THEN NOW() ELSE NULL END "
                "WHERE id = :id"
            ),
            {"id": task_id, "status": status, "result": result},
        )

    async def receipt(self, task_id: UUID, actor: UUID, key: UUID) -> dict | None:
        """Find an actor-scoped receipt before rejecting a stale expected version."""
        result = await self.db.execute(
            text(
                "SELECT action, payload, response FROM task_review_events "
                "WHERE task_id = :id AND actor_agent_id = :actor AND idempotency_key = :key"
            ),
            {"id": task_id, "actor": actor, "key": key},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def record(self, event: dict) -> None:
        """Insert a durable receipt without updating earlier receipts."""
        await self.db.execute(
            text(
                "INSERT INTO task_review_events (task_id, actor_agent_id, action, "
                "idempotency_key, expected_version, payload, response) "
                "VALUES (:task_id, :actor, :action, :key, :expected_version, "
                "CAST(:payload AS jsonb), CAST(:response AS jsonb))"
            ),
            {
                **event,
                "payload": json.dumps(event["payload"]),
                "response": json.dumps(event["response"]),
            },
        )

    async def history(self, task_id: UUID) -> list[dict]:
        """Return only the newest 50 transitions for one task."""
        result = await self.db.execute(
            text(
                "SELECT actor_agent_id, action, expected_version, payload->>'reason' AS reason, "
                "response, created_at "
                "FROM task_review_events WHERE task_id = :id ORDER BY id DESC LIMIT 50"
            ),
            {"id": task_id},
        )
        return [dict(row) for row in result.mappings()]

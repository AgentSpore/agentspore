"""Educational accounting SQL under the caller's transaction and row locks."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class LearningBudgetRepository:
    """Keep account creation and locking in deterministic UUID order."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def read(self, agent_id: UUID) -> dict | None:
        """Read a budget without creating an account or awarding a grant."""
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT available, reserved, initial_grant_applied "
                        "FROM learning_budget_accounts WHERE agent_id = :id"
                    ),
                    {"id": agent_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def lock_accounts(self, agent_ids: list[UUID]) -> dict:
        """Insert missing rows before locking, using the same order for both operations."""
        ordered = sorted(set(agent_ids), key=str)
        for agent_id in ordered:
            await self.db.execute(
                text(
                    "INSERT INTO learning_budget_accounts(agent_id) VALUES (:id) "
                    "ON CONFLICT (agent_id) DO NOTHING"
                ),
                {"id": agent_id},
            )
        rows = (
            await self.db.execute(
                text(
                    "SELECT * FROM learning_budget_accounts "
                    "WHERE agent_id = ANY(CAST(:ids AS uuid[])) ORDER BY agent_id FOR UPDATE"
                ),
                {"ids": ordered},
            )
        ).mappings()
        return {str(row["agent_id"]): dict(row) for row in rows}

    async def grant(self, agent_id: UUID) -> dict:
        """Apply the one-time grant without overwriting previously received units."""
        await self.lock_accounts([agent_id])
        row = (
            (
                await self.db.execute(
                    text(
                        "UPDATE learning_budget_accounts SET available = available + "
                        "CASE WHEN initial_grant_applied THEN 0 ELSE 100 END, "
                        "initial_grant_applied = TRUE WHERE agent_id = :id "
                        "RETURNING available, reserved, initial_grant_applied"
                    ),
                    {"id": agent_id},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def reservation(self, task_id: UUID, *, lock: bool = False) -> dict | None:
        """Read an immutable price, optionally serializing its settlement."""
        query = "SELECT * FROM learning_budget_reservations WHERE task_id = :id"
        if lock:
            query += " FOR UPDATE"
        row = (await self.db.execute(text(query), {"id": task_id})).mappings().first()
        return dict(row) if row else None

    async def reserve(self, task_id: UUID, requester: UUID, amount: int) -> None:
        """Move available units into one task reservation after account authorization."""
        await self.db.execute(
            text(
                "UPDATE learning_budget_accounts SET available = available - :amount, "
                "reserved = reserved + :amount WHERE agent_id = :id"
            ),
            {"amount": amount, "id": requester},
        )
        await self.db.execute(
            text(
                "INSERT INTO learning_budget_reservations(task_id, requester_agent_id, amount) "
                "VALUES (:task, :requester, :amount)"
            ),
            {"task": task_id, "requester": requester, "amount": amount},
        )

    async def settle(self, reservation: dict, recipient: UUID | None) -> None:
        """Release reserved units and credit the recipient or refund the requester."""
        requester, amount = reservation["requester_agent_id"], reservation["amount"]
        await self.db.execute(
            text(
                "UPDATE learning_budget_accounts SET reserved = reserved - :amount "
                "WHERE agent_id = :id"
            ),
            {"id": requester, "amount": amount},
        )
        await self.db.execute(
            text(
                "UPDATE learning_budget_accounts SET available = available + :amount "
                "WHERE agent_id = :id"
            ),
            {"id": recipient or requester, "amount": amount},
        )
        await self.db.execute(
            text("UPDATE learning_budget_reservations SET status = :status WHERE task_id = :id"),
            {"id": reservation["task_id"], "status": "captured" if recipient else "refunded"},
        )

    async def project_has_reservations(self, project_id: UUID) -> bool:
        """Detect restricted educational history before deleting a project."""
        return bool(
            await self.db.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM learning_budget_reservations r "
                    "JOIN tasks t ON t.id = r.task_id WHERE t.project_id = :id)"
                ),
                {"id": project_id},
            )
        )

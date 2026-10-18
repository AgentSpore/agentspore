"""Requester-controlled acceptance with task-first locks and durable idempotency."""

from uuid import UUID, uuid4

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.agent_repo import AgentRepository
from app.repositories.task_review_repo import TaskReviewRepository
from app.schemas.task_reviews import (
    TaskReviewDetail,
    TaskReviewRequest,
    TaskReviewResponse,
    TaskSubmitRequest,
)
from app.services.learning_budget_service import LearningBudgetService


class TaskReviewService:
    """Keep authorization and transition checks on the same locked request rows."""

    final_states = frozenset({"accepted", "rejected", "cancelled"})

    def __init__(self, db: AsyncSession):
        self.tasks = AgentRepository(db)
        self.repo = TaskReviewRepository(db)
        self.budgets = LearningBudgetService(db)

    async def load(self, task_id: UUID) -> tuple[dict, dict | None]:
        """Preserve legacy snapshots; lock reviewed tasks before their review rows."""
        task = await self.tasks.get_task_by_id(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if await self.repo.get_review(task_id, lock=False) is None:
            return task, None
        task = await self.tasks.lock_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return task, await self.repo.get_review(task_id)

    async def create(self, task: dict, requester: UUID) -> None:
        """Attach the review and creation receipt inside the creating transaction."""
        await self.tasks.lock_task(task["id"])
        await self.repo.create(task["id"], requester)
        review = {
            "state": "awaiting_submission",
            "version": 0,
            "submission_version": 0,
            "relationship": "unknown",
        }
        await self.repo.record(
            {
                "task_id": task["id"],
                "actor": requester,
                "action": "create",
                "key": uuid4(),
                "expected_version": 0,
                "payload": {},
                "response": TaskReviewResponse.from_rows(task, review).model_dump(mode="json"),
            }
        )

    @staticmethod
    def _authorize(task: dict, review: dict, actor: UUID, action: str) -> None:
        if action == "submit" and task["claimed_by_agent_id"] is None:
            raise HTTPException(status_code=409, detail="Task has no claimant")
        principal = (
            task["claimed_by_agent_id"] if action == "submit" else review["requester_agent_id"]
        )
        if str(principal) != str(actor):
            raise HTTPException(status_code=403, detail="Only the assigned task role may act")

    async def detail(self, task_id: UUID, actor: UUID) -> TaskReviewDetail:
        """Read a consistent authorized snapshot and bounded receipt history."""
        task, review = await self.load(task_id)
        if review is None:
            raise HTTPException(status_code=409, detail="Task does not require review")
        if str(actor) not in {str(review["requester_agent_id"]), str(task["claimed_by_agent_id"])}:
            raise HTTPException(status_code=403, detail="Only task participants may read review")
        return TaskReviewDetail(
            **TaskReviewResponse.from_rows(task, review).model_dump(),
            learning_reservation=await self.budgets.repo.reservation(task_id),
            result=review["result"],
            history=await self.repo.history(task_id),
        )

    async def transition(
        self,
        task_id: UUID,
        actor: UUID,
        body: TaskSubmitRequest | TaskReviewRequest,
    ) -> TaskReviewResponse:
        """Replay authorized receipts or atomically apply one expected-version transition."""
        task, review = await self.load(task_id)
        if review is None:
            raise HTTPException(status_code=409, detail="Task does not require review")
        action = "submit" if isinstance(body, TaskSubmitRequest) else body.action
        self._authorize(task, review, actor, action)
        payload = body.model_dump(mode="json", exclude={"idempotency_key"})
        previous = await self.repo.receipt(task_id, actor, body.idempotency_key)
        if previous is not None:
            if previous["action"] != action or previous["payload"] != payload:
                raise HTTPException(status_code=409, detail="Idempotency key payload differs")
            return TaskReviewResponse.model_validate(previous["response"])
        if review["version"] != body.expected_version:
            raise HTTPException(status_code=409, detail="Review version differs")
        self._apply(task, review, body)
        review["version"] += 1
        await self.budgets.settle(task, review["state"])
        await self.repo.update(review)
        if review["state"] in self.final_states:
            await self.repo.finish_task(task_id, task["status"], review["result"])
        response = TaskReviewResponse.from_rows(task, review)
        await self.repo.record(
            {
                "task_id": task_id,
                "actor": actor,
                "action": action,
                "key": body.idempotency_key,
                "expected_version": body.expected_version,
                "payload": payload,
                "response": response.model_dump(mode="json"),
            }
        )
        return response

    def _apply(self, task: dict, review: dict, body: TaskSubmitRequest | TaskReviewRequest) -> None:
        if review["state"] in self.final_states:
            raise HTTPException(status_code=409, detail="Review is final")
        if isinstance(body, TaskSubmitRequest):
            if task["status"] != "claimed" or review["state"] not in {
                "awaiting_submission",
                "changes_requested",
            }:
                raise HTTPException(status_code=409, detail="Task cannot accept a submission")
            review.update(
                state="submitted",
                result=body.result,
                submission_version=review["submission_version"] + 1,
            )
            return
        if task["status"] not in {"open", "claimed"}:
            raise HTTPException(status_code=409, detail="Task is not open or claimed")
        if body.action != "cancel" and (
            review["state"] != "submitted" or task["status"] != "claimed"
        ):
            raise HTTPException(status_code=409, detail="A submitted result is required")
        review["state"] = {
            "accept": "accepted",
            "request_changes": "changes_requested",
            "reject": "rejected",
            "cancel": "cancelled",
        }[body.action]
        if body.action in {"accept", "reject", "cancel"}:
            task["status"] = "completed" if body.action == "accept" else "cancelled"

    async def claimed(self, task: dict, review: dict, actor: UUID) -> None:
        """Advance claim version and classify the current relationship without rewards."""
        review["relationship"] = await self.repo.relationship(review["requester_agent_id"], actor)
        await self._assignment_event(task, review, actor, "claim")

    async def unclaimed(self, task: dict, review: dict, actor: UUID) -> None:
        """Advance a pre-submission release without changing the fixed requester."""
        review["relationship"] = "unknown"
        await self._assignment_event(task, review, actor, "unclaim")

    async def _assignment_event(self, task: dict, review: dict, actor: UUID, action: str) -> None:
        version = review["version"]
        review["version"] += 1
        await self.repo.update(review)
        await self.repo.record(
            {
                "task_id": task["id"],
                "actor": actor,
                "action": action,
                "key": uuid4(),
                "expected_version": version,
                "payload": {},
                "response": TaskReviewResponse.from_rows(task, review).model_dump(mode="json"),
            }
        )


def get_task_review_service(db: AsyncSession = Depends(get_db)) -> TaskReviewService:
    """Inject the transaction owned by the request dependency."""
    return TaskReviewService(db)

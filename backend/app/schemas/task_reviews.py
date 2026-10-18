"""Versioned requester acceptance requests and durable response receipts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.learning_budget import LearningReservation

ReviewState = Literal[
    "awaiting_submission", "submitted", "changes_requested", "accepted", "rejected", "cancelled"
]
ReviewAction = Literal["accept", "request_changes", "reject", "cancel"]
Relationship = Literal["internal", "external_unverified", "unknown"]


class TaskSubmitRequest(BaseModel):
    """A claimant's text submission against one known review version."""

    model_config = ConfigDict(extra="forbid")
    result: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16000)]
    expected_version: int = Field(ge=0)
    idempotency_key: UUID


class TaskReviewRequest(BaseModel):
    """The fixed requester's decision against one known review version."""

    model_config = ConfigDict(extra="forbid")
    action: ReviewAction
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] = ""
    expected_version: int = Field(ge=0)
    idempotency_key: UUID


class TaskReviewResponse(BaseModel):
    """Persisted receipt returned unchanged on an authorized identical retry."""

    task_id: str
    task_status: str
    state: ReviewState
    version: int
    submission_version: int
    relationship: Relationship

    @classmethod
    def from_rows(cls, task: dict, review: dict) -> "TaskReviewResponse":
        """Construct the wire response from the locked task and review rows."""
        return cls(
            task_id=str(task["id"]),
            task_status=task["status"],
            **{
                key: review[key]
                for key in ("state", "version", "submission_version", "relationship")
            },
        )


class TaskReviewEvent(BaseModel):
    """One accepted transition, omitting private idempotency payloads."""

    actor_agent_id: UUID
    action: str
    expected_version: int
    reason: str | None = Field(default=None, max_length=4000)
    response: TaskReviewResponse
    created_at: datetime


class TaskReviewDetail(TaskReviewResponse):
    """Current result and the newest 50 transition receipts."""

    learning_reservation: LearningReservation | None = None
    result: str | None
    history: list[TaskReviewEvent]

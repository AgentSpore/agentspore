"""Safe owner-facing projections for external agents and their task history."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.learning_budget import LearningBudgetResponse, LearningReservation
from app.schemas.task_reviews import Relationship, ReviewState


class WorkspaceAgentRef(BaseModel):
    """Public participant identity without account or authentication fields."""

    id: UUID
    name: str
    handle: str | None


class WorkspaceBudget(LearningBudgetResponse):
    """Educational balances and separately directed settlement totals."""

    transferred: int = 0
    received: int = 0
    refunded: int = 0


class WorkspaceAgent(WorkspaceAgentRef):
    """An owner's external agent with observed heartbeat time."""

    specialization: str
    is_active: bool
    created_at: datetime | None
    last_heartbeat: datetime | None
    budget: WorkspaceBudget


class WorkspaceReviewCounts(BaseModel):
    """Count each visible review once, including finalized tasks."""

    awaiting_submission: int = 0
    submitted: int = 0
    changes_requested: int = 0
    accepted: int = 0
    rejected: int = 0
    cancelled: int = 0


class AgentWorkspaceResponse(BaseModel):
    """Owner-scoped external agents and review work awaiting attention."""

    agents: list[WorkspaceAgent]
    review_counts: WorkspaceReviewCounts


class WorkspaceProject(BaseModel):
    """Project identity without arbitrary project metadata."""

    id: UUID
    title: str | None


class WorkspaceTask(BaseModel):
    """Visible task metadata; results exist only in the authorized detail."""

    id: UUID
    title: str
    description: str | None
    project: WorkspaceProject | None
    requester: WorkspaceAgentRef | None
    claimant: WorkspaceAgentRef | None
    viewer_roles: list[Literal["requester", "claimant"]]
    task_status: str | None
    review_state: ReviewState | None
    review_version: int | None
    relationship: Relationship | None
    created_at: datetime | None
    learning_reservation: LearningReservation | None


class WorkspaceTaskPage(BaseModel):
    """A bounded deterministic page and the owner's total visible task count."""

    items: list[WorkspaceTask]
    total: int
    limit: int
    offset: int


class WorkspaceEvent(BaseModel):
    """A safe event projection omitting payload and idempotency identifiers."""

    actor: WorkspaceAgentRef
    action: str
    reason: str | None
    state: ReviewState
    version: int
    created_at: datetime


class WorkspaceTaskDetail(WorkspaceTask):
    """Private result and the newest fifty authorized review events."""

    result: str | None
    history: list[WorkspaceEvent]

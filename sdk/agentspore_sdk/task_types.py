"""Typed JSON contracts for manual tasks, acceptance, and educational units."""

from typing import Literal, NotRequired, TypedDict

ReviewAction = Literal["accept", "request_changes", "reject", "cancel"]
ReviewState = Literal[
    "awaiting_submission",
    "submitted",
    "changes_requested",
    "accepted",
    "rejected",
    "cancelled",
]
Relationship = Literal["internal", "external_unverified", "unknown"]


class TaskCreateRequest(TypedDict):
    """Creator payload; keep the same UUID key and content for retries."""

    idempotency_key: str
    title: str
    description: str
    type: NotRequired[Literal["write_docs"]]
    review_required: NotRequired[bool]
    learning_credit_amount: NotRequired[int | None]


class TaskCreateResponse(TypedDict):
    """Identity and current status, including an existing retry result."""

    task_id: str
    status: str
    created: bool


class TaskClaimResponse(TypedDict):
    """Claim or unclaim receipt."""

    task_id: str
    status: str
    message: str


class TaskFilters(TypedDict, total=False):
    """Optional filters for the public open-task list."""

    type: str
    project_id: str
    limit: int


class OpenTask(TypedDict):
    """Open task with nullable project and educational price."""

    id: str
    project_id: str | None
    project_title: str | None
    type: str
    title: str
    description: str
    priority: str
    status: str
    source_type: str | None
    created_at: str
    review_required: bool
    review_version: int | None
    learning_credit_amount: int | None


class TaskSubmitRequest(TypedDict):
    """Claimant text against a known version; retries keep every field."""

    result: str
    expected_version: int
    idempotency_key: str


class TaskReviewRequest(TypedDict):
    """Requester decision against a known version."""

    action: ReviewAction
    expected_version: int
    idempotency_key: str
    reason: NotRequired[str]


class TaskReviewResponse(TypedDict):
    """Durable receipt, not a fresh snapshot on an identical retry."""

    task_id: str
    task_status: str
    state: ReviewState
    version: int
    submission_version: int
    relationship: Relationship


class LearningReservation(TypedDict):
    """Immutable educational price and settlement state."""

    amount: int
    status: Literal["reserved", "captured", "refunded"]


class TaskReviewEvent(TypedDict):
    """One accepted transition, without private retry payloads."""

    actor_agent_id: str
    action: str
    expected_version: int
    reason: str | None
    response: TaskReviewResponse
    created_at: str


class TaskReviewDetail(TaskReviewResponse):
    """Current review, result, newest fifty transitions, and reservation."""

    result: str | None
    history: list[TaskReviewEvent]
    learning_reservation: LearningReservation | None


class LearningBudgetResponse(TypedDict):
    """Educational units; these are not money or withdrawal balances."""

    available: int
    reserved: int
    initial_grant_applied: bool

"""Battle enums and row models (V66).

The enums mirror the CHECK constraints in db/migrations/V66__battles.sql one
for one. They exist so service and repository code names a state instead of
spelling a bare string that a typo turns into a silent no-op CAS: a
compare-and-set against a misspelled status matches zero rows and looks
exactly like losing a race.

The database remains the authority. These are a convenience over it, never a
replacement for it — the constraints are what actually make an invalid state
unrepresentable.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class BattleStatus(str, Enum):
    """Lifecycle of a battle. See the V66 header for the full narrative."""

    CHALLENGE_PENDING = "challenge_pending"
    ACCEPTED = "accepted"
    RESERVED = "reserved"
    QUEUED = "queued"
    RUNNING = "running"
    JUDGING = "judging"
    COMPLETED = "completed"
    DECLINED = "declined"
    EXPIRED = "expired"
    ABORTED = "aborted"


# A terminal battle is finished forever: every transition out of one must
# return zero rows. Kept next to the enum so a new status cannot be added
# without deciding which side of this line it falls on.
TERMINAL_STATUSES: frozenset[BattleStatus] = frozenset(
    {
        BattleStatus.COMPLETED,
        BattleStatus.DECLINED,
        BattleStatus.EXPIRED,
        BattleStatus.ABORTED,
    }
)


class Winner(str, Enum):
    """Verdict axis. NULL winner on a completed battle means no quorum."""

    A = "a"
    B = "b"
    TIE = "tie"


class Side(str, Enum):
    """Which fighter a submission belongs to."""

    A = "a"
    B = "b"


class Vote(str, Enum):
    """A judge's verdict.

    ABSTAIN and ERROR are excluded from the quorum denominator and are
    deliberately distinct from TIE: malformed judge output must never mint
    tie-Elo for the fighters.
    """

    A = "a"
    B = "b"
    TIE = "tie"
    ABSTAIN = "abstain"
    ERROR = "error"


class PresentedOrder(str, Enum):
    """Which fighter was shown first — the position-bias control.

    Part of the raw-run key, because one replicate is two runs.
    """

    AB = "ab"
    BA = "ba"


class JudgeKind(str, Enum):
    """LLM replicate or a human oracle (phase 2)."""

    LLM = "llm"
    HUMAN = "human"


class JudgeRunStatus(str, Enum):
    """Lifecycle of a single raw judge run, claimed via its lease token."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskSource(str, Enum):
    """Where a battle task came from. COMPANY awaits phase 3."""

    GENERATED = "generated"
    COMPANY = "company"


class TaskStatus(str, Enum):
    """Whether a task may be used for new battles."""

    DRAFT = "draft"
    READY = "ready"
    RETIRED = "retired"


class BattleTask(BaseModel):
    """A row of battle_tasks."""

    id: UUID
    source: TaskSource
    org_id: UUID | None = None
    title: str
    prompt: str
    rubric: list[dict[str, Any]]
    category: str | None = None
    time_limit_seconds: int
    status: TaskStatus
    created_by_user_id: UUID | None = None
    created_at: datetime


class Battle(BaseModel):
    """A row of battles.

    The four facts the design keeps apart live in separate fields on purpose:
    owner consent (agent_b_accepted_at), live handoff (not stored here — it is
    a momentary transport claim, not state), agent ACK (agent_events.acked_at),
    and battle readiness (readiness_generation + ready_check_event_id_a/b).
    """

    id: UUID
    task_id: UUID
    status: BattleStatus

    agent_a_id: UUID
    agent_b_id: UUID | None = None
    agent_a_owner_snapshot: UUID
    agent_b_owner_snapshot: UUID | None = None

    agent_b_accepted_at: datetime | None = None
    challenge_expires_at: datetime

    ready_lease_expires_at: datetime | None = None
    readiness_generation: int = 0
    ready_check_event_id_a: UUID | None = None
    ready_check_event_id_b: UUID | None = None

    task_prompt_snapshot: str
    task_rubric_snapshot: list[dict[str, Any]]
    time_limit_seconds_snapshot: int

    winner: Winner | None = None
    verdict_reason: str | None = None
    elo_a_before: int | None = None
    elo_b_before: int | None = None
    elo_a_after: int | None = None
    elo_b_after: int | None = None

    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None
    lease_attempt_count: int = 0

    challenged_at: datetime
    queued_at: datetime | None = None
    started_at: datetime | None = None
    deadline_at: datetime | None = None
    finalized_at: datetime | None = None
    ended_at: datetime | None = None


class BattleReservation(BaseModel):
    """A row of battle_reservations — one agent, one active battle."""

    agent_id: UUID
    battle_id: UUID
    reserved_until: datetime
    created_at: datetime


class BattleSubmission(BaseModel):
    """A row of battle_submissions — one checkpoint from one side."""

    battle_id: UUID
    side: Side
    seq_no: int
    content: str | None = None
    tokens_used: int | None = None
    is_final: bool = False
    received_at: datetime
    finished_at: datetime | None = None
    truncated: bool = False
    error: str | None = None


class BattleJudgeRun(BaseModel):
    """A row of battle_judge_runs — one half of an ab/ba replicate pair."""

    id: UUID
    battle_id: UUID
    judge_kind: JudgeKind
    judge_ref: str
    replicate_seed: str
    presented_order: PresentedOrder
    status: JudgeRunStatus
    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0
    vote: Vote | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reasoning: str | None = None
    scores: dict[str, Any] | None = None
    created_at: datetime
    completed_at: datetime | None = None


class BattleJudgement(BaseModel):
    """A row of battle_judgements — one collapsed vote per replicate."""

    id: UUID
    battle_id: UUID
    judge_kind: JudgeKind
    judge_ref: str
    replicate_seed: str
    vote: Vote
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reasoning: str | None = None
    scores: dict[str, Any] | None = None
    position_sensitive: bool = False
    created_at: datetime

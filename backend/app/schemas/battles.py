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

# Hard ceiling on ONE submission, enforced at the API edge before the body is
# stored or judged. Mirrors battle_judges.MAX_SUBMISSION_CHARS: a submission the
# judge would truncate anyway is not worth persisting in full, and without a cap
# a fighter can deny judging by submitting a novel.
#
# CHARS, not bytes, and the name now says so. pydantic's max_length counts
# characters, so the previous name (MAX_SUBMISSION_BYTES) promised a byte cap
# this never enforced: 12k characters of multi-byte UTF-8 is up to ~48kB. The
# limit is deliberately left in characters — it exists to bound what the judge
# must read, and a judge reads characters — but a name that lies about its unit
# is how someone later "fixes" the wrong end of it.
MAX_SUBMISSION_CHARS = 12_000


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


class CreateChallengeRequest(BaseModel):
    """Open a challenge. ``agent_b_id`` omitted = an open challenge.

    The challenger's owner is never a field: it comes from the JWT and is
    verified against the agents row. Accepting it from the body would let a
    caller challenge with an agent they do not own.
    """

    task_id: UUID
    agent_a_id: UUID
    agent_b_id: UUID | None = None


class CreateTaskRequest(BaseModel):
    """Admin-generated battle task."""

    title: str = Field(..., min_length=1, max_length=300)
    prompt: str = Field(..., min_length=1, max_length=20_000)
    rubric: list[dict[str, Any]] = Field(..., min_length=1)
    category: str | None = Field(default=None, max_length=50)
    time_limit_seconds: int = Field(default=600, gt=0, le=3600)


class BattleSummary(BaseModel):
    """Public view of a battle.

    Judge verdicts are deliberately absent — see BattleDetail, which reveals
    them only once the battle is completed.
    """

    id: UUID
    task_id: UUID
    status: BattleStatus
    agent_a_id: UUID
    agent_b_id: UUID | None = None
    winner: Winner | None = None
    challenged_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None


class ReadinessView(BaseModel):
    """Both fighters' readiness, reported as the two distinct facts it is.

    ``accepted`` is owner consent; ``acked`` is a current-generation ready-ACK.
    They are separate fields because they are separate facts: a consented
    battle whose agent never acked is a real and common state, and rendering
    one from the other would report a readiness nobody proved.
    """

    generation: int
    lease_expires_at: datetime | None = None
    accepted: bool
    ready: bool


class BattleDetail(BattleSummary):
    """One battle in full, for the owner and spectator views."""

    agent_a_owner_snapshot: UUID
    agent_b_owner_snapshot: UUID | None = None
    agent_b_accepted_at: datetime | None = None
    challenge_expires_at: datetime
    task_prompt_snapshot: str
    task_rubric_snapshot: list[dict[str, Any]]
    time_limit_seconds_snapshot: int
    verdict_reason: str | None = None
    elo_a_before: int | None = None
    elo_b_before: int | None = None
    elo_a_after: int | None = None
    elo_b_after: int | None = None
    queued_at: datetime | None = None
    deadline_at: datetime | None = None
    readiness: ReadinessView | None = None


class BattleSubmissionView(BaseModel):
    """One submission, as a spectator may see it.

    ``content`` is Optional here for a reason that is a rule, not a nullable
    column: while a battle is RUNNING it is withheld from everyone. Showing A's
    checkpoint to B mid-battle would let B read the answer it is competing
    against and copy it — the endpoint would become the cheating tool. The
    metadata around it (which side, how many checkpoints, is_final, truncated)
    is safe throughout and is what "submitted / timed out / never answered" is
    rendered from, so the live view loses nothing it is entitled to.

    ``error`` is safe to expose because the column holds an exception TYPE only,
    never a value (V66:349) — a provider message can carry the fighter's prompt
    or key material, so it never reaches the table in the first place.
    """

    side: Side
    seq_no: int
    is_final: bool
    truncated: bool
    error: str | None = None
    received_at: datetime
    tokens_used: int | None = None
    content: str | None = None
    content_withheld: bool = False


class BattleJudgeRunView(BaseModel):
    """One RAW half of a replicate pair — the evidence, not the claim.

    Exposed on purpose, and only after 'completed'. ``presented_order`` and
    ``replicate_seed`` are internals, but they are precisely what lets a viewer
    CHECK the position-bias control instead of taking our word for it: two rows
    per seed, one 'ab' and one 'ba', is the pairing made auditable. A verdict a
    spectator can only believe is worth less than one they can recompute.

    ``replicate_seed`` leaks nothing: it is hash(battle_id, replicate_no).
    """

    judge_kind: JudgeKind
    judge_ref: str
    replicate_seed: str
    presented_order: PresentedOrder
    status: JudgeRunStatus
    vote: Vote | None = None
    confidence: float | None = None
    reasoning: str | None = None
    scores: dict[str, Any] | None = None


class BattleJudgementView(BaseModel):
    """One COLLAPSED vote — one replicate, one voice."""

    judge_kind: JudgeKind
    judge_ref: str
    replicate_seed: str
    vote: Vote
    confidence: float | None = None
    reasoning: str | None = None
    scores: dict[str, Any] | None = None
    position_sensitive: bool = False


class JudgeTally(BaseModel):
    """Quorum arithmetic for ONE judge kind, shown rather than asserted.

    Kept per-kind because LLM replicates and human votes do not share a quorum:
    three correlated samples of one model are not three judges, and averaging
    them together with people would launder that distinction. ``abstained`` and
    ``errored`` are reported next to ``valid`` because they are excluded from the
    denominator — a reader must be able to see what was thrown away.
    """

    votes_for_a: int = 0
    votes_for_b: int = 0
    ties: int = 0
    abstained: int = 0
    errored: int = 0
    valid: int = 0
    position_sensitive: int = 0


class BattleVerdictView(BaseModel):
    """Everything needed to audit a completed battle's verdict."""

    judgements: list[BattleJudgementView]
    runs: list[BattleJudgeRunView]
    tallies: dict[str, JudgeTally]


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


class SubmitTurnRequest(BaseModel):
    """One checkpoint from a fighter, posted to /battles/{id}/turns.

    Notice what is NOT here: any timestamp. A fighter must never be able to
    state when it finished — the deadline is wall-clock and server-owned (V66
    derives deadline_at inside the transition statement), so accepting a
    client's clock would let a late agent claim it answered on time. The server
    times the arrival, and the request cannot even express an opinion.

    ``tokens_used`` is self-reported and is therefore telemetry, not currency:
    nothing rests on it. It is stored as the fighter's claim about its own cost.
    """

    content: str = Field(..., max_length=MAX_SUBMISSION_CHARS)
    seq_no: int = Field(..., ge=1, description="Monotonic per side; a taken slot is a conflict.")
    is_final: bool = Field(default=False, description="One-way: the last word for this side.")
    tokens_used: int | None = Field(default=None, ge=0)


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

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

from pydantic import BaseModel, Field, model_validator

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
    # A registered user's submission (V70). Never reaches the rated pool without
    # a moderator: the battle_task_ready_requires_approval CHECK keys on
    # source <> 'generated', not on this member specifically.
    USER = "user"


class TaskStatus(str, Enum):
    """Whether a task may be used for new battles.

    DRAFT/READY/RETIRED keep their V66 meaning. The three V70 members are the
    submission lifecycle: PENDING_VALIDATION (accepted, not yet judged by the
    validator — including "the LLM budget was spent, try again later"),
    QUARANTINE (validated, playable only in battles that cannot move Elo), and
    REJECTED (terminal; a rejected row is outside the dedup index so a corrected
    resubmission is never blocked by it).
    """

    DRAFT = "draft"
    READY = "ready"
    RETIRED = "retired"
    PENDING_VALIDATION = "pending_validation"
    QUARANTINE = "quarantine"
    REJECTED = "rejected"


class TaskDifficulty(str, Enum):
    """The rated-track difficulty vocabulary (V67).

    Mirrors the ``battle_task_difficulty_enum`` CHECK one for one. A closed set,
    never a free string: a battle's difficulty FILTER is matched against a
    task's concrete difficulty at binding time, and a typo'd filter would
    silently match nothing and abort every rated challenge for that combination.
    NULL on a battle filter means "any"; a task row always has a concrete value.
    """

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class BattleTask(BaseModel):
    """A row of battle_tasks.

    Internal only after V67 — the public catalog route no longer returns task
    rows, only pool aggregates (:class:`BattleTaskPool`). Kept for the admin
    creation path and internal callers.
    """

    id: UUID
    source: TaskSource
    org_id: UUID | None = None
    title: str
    prompt: str
    rubric: list[dict[str, Any]]
    category: str
    difficulty: TaskDifficulty = TaskDifficulty.MEDIUM
    time_limit_seconds: int
    status: TaskStatus
    last_used_at: datetime | None = None
    use_count: int = 0
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
    # Unbound until reserved -> queued (V67): a challenge carries a filter, not
    # a task. NULL through every pre-queue state, set only at binding.
    task_id: UUID | None = None
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

    # The requested filter, frozen at challenge time. NULL means "any".
    task_category_filter: str | None = None
    task_difficulty_filter: TaskDifficulty | None = None

    # The bound task's snapshot. All NULL until binding, all set after — the
    # all-or-nothing shape the V67 CHECK enforces.
    task_title_snapshot: str | None = None
    task_prompt_snapshot: str | None = None
    task_rubric_snapshot: list[dict[str, Any]] | None = None
    time_limit_seconds_snapshot: int | None = None

    winner: Winner | None = None
    verdict_reason: str | None = None

    # Rated-track state (V68). rated_eligible is the frozen acceptance decision
    # (NULL = undecided), is_rated the final settled outcome (NULL until
    # completed), rated_ineligibility_reason names why an unrated battle is
    # unrated. Owner-snapshot ids are still never shipped publicly; these three
    # carry no ownership information.
    rated_eligible: bool | None = None
    is_rated: bool | None = None
    rated_ineligibility_reason: str | None = None
    judging_stop_reason: str | None = None

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

    The challenger names a task CATEGORY and DIFFICULTY, never a task id (V67):
    the concrete task is chosen and snapshotted only after both fighters prove
    readiness, so no side can precompute an answer to a task it picked. Both
    filters are nullable; NULL means "any". The wire never carries the string
    ``"any"`` — the UI translates its "Любая" selection to JSON ``null``.

    The challenger's owner is never a field: it comes from the JWT and is
    verified against the agents row. Accepting it from the body would let a
    caller challenge with an agent they do not own.
    """

    task_category: str | None = Field(default=None, min_length=1, max_length=50)
    task_difficulty: TaskDifficulty | None = None
    agent_a_id: UUID
    agent_b_id: UUID | None = None


class CreateTaskRequest(BaseModel):
    """Admin-generated battle task.

    Category and difficulty are REQUIRED (V67): a task with no category or an
    arbitrary difficulty could never be reached by a filtered rated challenge,
    and the binding pool is bucketed by exactly these two fields.
    """

    title: str = Field(..., min_length=1, max_length=300)
    prompt: str = Field(..., min_length=1, max_length=20_000)
    rubric: list[dict[str, Any]] = Field(..., min_length=1)
    category: str = Field(..., min_length=1, max_length=50)
    difficulty: TaskDifficulty
    time_limit_seconds: int = Field(default=600, gt=0, le=3600)


class SubmitTaskRequest(BaseModel):
    """A registered user's proposed battle task (V70).

    The same fields as :class:`CreateTaskRequest` and deliberately its own model:
    the admin route's body is free to gain generator-only options, and a
    submission body must never inherit one by accident. The bounds here are the
    outer envelope only — the validator applies its own, tighter, checks before
    spending an LLM call.
    """

    title: str = Field(..., min_length=1, max_length=300)
    prompt: str = Field(..., min_length=1, max_length=20_000)
    rubric: list[dict[str, Any]] = Field(..., min_length=1, max_length=20)
    category: str = Field(..., min_length=1, max_length=50)
    difficulty: TaskDifficulty
    time_limit_seconds: int = Field(default=600, gt=0, le=3600)


class SubmitTaskResponse(BaseModel):
    """What the submitter learns the moment their task is stored.

    ``status`` may already be terminal (a cheap filter rejected it) or still
    ``pending_validation`` — including when the LLM budget was spent, which is
    not a verdict and must not read like one.
    """

    id: UUID
    status: TaskStatus
    reason: str | None = None


class UserTaskSummary(BaseModel):
    """One of the caller's own submissions.

    Carries the prompt because it is the caller's own text; V67 pool secrecy is
    about OTHER people's tasks. ``quarantine_battles`` is shown so a submitter
    can see their task is being played while it waits for approval.
    """

    id: UUID
    title: str
    prompt: str
    category: str
    difficulty: TaskDifficulty
    status: TaskStatus
    validation_reason: str | None = None
    quarantine_battles: int
    use_count: int
    approved_at: datetime | None = None
    created_at: datetime


class ModerationTaskView(BaseModel):
    """One row of the moderator queue, with the evidence approval needs.

    The quarantine record (battles served, decisive results) is part of the row
    rather than a separate lookup: approval is the act that lets a task move real
    Elo, and the collusion signal author exclusion cannot catch is an anomalous
    record over exactly these battles.
    """

    id: UUID
    title: str
    prompt: str
    rubric: list[dict[str, Any]]
    category: str
    difficulty: TaskDifficulty
    status: TaskStatus
    author_user_id: UUID | None = None
    validation_reason: str | None = None
    validation_verdict: dict[str, Any] | None = None
    quarantine_battles: int
    use_count: int
    settled_battles: int
    decisive_battles: int
    created_at: datetime


class RejectTaskRequest(BaseModel):
    """A moderator's rejection. The reason is shown to the submitter."""

    reason: str = Field(..., min_length=1, max_length=500)


class BattleTaskPool(BaseModel):
    """One (category, difficulty) bucket of the fresh task pool (V67).

    The public catalog replacement: it carries COUNTS, never task content. A
    caller learns which filter combinations can currently accept a rated
    challenge without ever seeing a task id, title, prompt or rubric.
    """

    category: str
    difficulty: TaskDifficulty
    fresh_count: int
    challenge_available: bool


class BattleTaskPoolsResponse(BaseModel):
    """The public ``GET /battles/tasks`` response — pool aggregates only."""

    minimum_pool_size: int
    cooldown_days: int
    pools: list[BattleTaskPool]


class BattleSummary(BaseModel):
    """Public view of a battle.

    Judge verdicts are deliberately absent — see BattleDetail, which reveals
    them only once the battle is completed.

    The bound task is WITHHELD until the battle is running (V67): ``task_id``
    and ``task_title_snapshot`` are nulled by the route for every pre-running
    row, and ``task_content_withheld`` says so explicitly rather than letting a
    null read as "no task". The requested filter (category/difficulty) is always
    safe to show — it reveals nothing about which concrete task was picked.
    """

    id: UUID
    task_id: UUID | None = None
    status: BattleStatus
    agent_a_id: UUID
    agent_b_id: UUID | None = None
    winner: Winner | None = None
    challenged_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None

    task_category_filter: str | None = None
    task_difficulty_filter: TaskDifficulty | None = None
    task_title_snapshot: str | None = None
    task_content_withheld: bool = False

    # Rated-track badge inputs (V68 F1). rated_eligible drives the pre-completion
    # "Rated"/"Rating pending"/"Unrated" badge; is_rated the completed
    # "Rated · Elo updated"/"Unrated" badge; the reason and public-safe
    # judging_stop_reason let the UI explain an unrated outcome. None of these
    # reveals an owner-snapshot id.
    rated_eligible: bool | None = None
    is_rated: bool | None = None
    rated_ineligibility_reason: str | None = None
    judging_stop_reason: str | None = None


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
    """One battle in full, for the owner and spectator views.

    The two owner-snapshot UUIDs are deliberately ABSENT. This DTO is served by
    a PUBLIC route (GET /battles/{id}), and shipping ``agent_*_owner_snapshot``
    let anyone enumerate which agents share an owner — the ownership graph the
    platform does not otherwise reveal. The one legitimate need those ids served
    on the client (does the signed-in viewer own the opponent, so may they
    accept/decline?) is answered instead by ``viewer_can_accept``, computed
    server-side from the caller's JWT so the raw owner ids never leave the
    backend.
    """

    agent_b_accepted_at: datetime | None = None
    challenge_expires_at: datetime
    # Withheld (nulled by the route) until the battle is running — see
    # ``task_content_withheld`` below and the status gate in the get_battle
    # route. Nullable in the schema too, because a still-unbound battle simply
    # has no snapshot yet.
    task_prompt_snapshot: str | None = None
    task_rubric_snapshot: list[dict[str, Any]] | None = None
    time_limit_seconds_snapshot: int | None = None
    verdict_reason: str | None = None
    elo_a_before: int | None = None
    elo_b_before: int | None = None
    elo_a_after: int | None = None
    elo_b_after: int | None = None
    queued_at: datetime | None = None
    deadline_at: datetime | None = None
    readiness: ReadinessView | None = None

    # Capability, not identity: TRUE only when the authenticated caller owns the
    # opponent of a still-pending challenge and may therefore accept or decline
    # it. Computed from the JWT on the server, defaulting FALSE for the
    # anonymous public reader — it replaces shipping the raw owner UUIDs, which
    # would have exposed the ownership graph to anyone.
    viewer_can_accept: bool = False


class CreateBattleBlockRequest(BaseModel):
    """Block another owner from battling any of your agents (V68 D).

    Exactly one of the two identifiers must be supplied. ``blocked_agent_id`` is
    the UI-preferred form: the server resolves it to the agent's current owner,
    so the block covers every current and future agent of that owner without the
    caller ever handling an owner id. ``blocked_owner_id`` is the direct API form.
    """

    blocked_agent_id: UUID | None = None
    blocked_owner_id: UUID | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> CreateBattleBlockRequest:
        if (self.blocked_agent_id is None) == (self.blocked_owner_id is None):
            raise ValueError(
                "exactly one of blocked_agent_id or blocked_owner_id is required"
            )
        return self


class BattleBlockResponse(BaseModel):
    """One owner-level block the caller created.

    Only the BLOCKED owner id is returned — never the blocker's, which is always
    the authenticated caller. An owner's block list is private to them.
    """

    id: UUID
    blocked_owner_id: UUID
    created_at: datetime


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
    # Upper-bounded well below the reconciler's SILENT_FIGHTER_SEQ_NO (9999): the
    # silent fighter's synthetic final lands at that sequence, and the partial
    # unique index makes exactly one final per side possible. A client allowed to
    # post seq_no >= 9999 could take that slot first and permanently block the
    # deadline synthesis, turning silence into a battle that can never be judged.
    seq_no: int = Field(
        ...,
        ge=1,
        le=9000,
        description="Monotonic per side; a taken slot is a conflict.",
    )
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

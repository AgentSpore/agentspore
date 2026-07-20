"""Battles API — challenge, consent, reservation and readiness (step 8).

Read endpoints are public; every mutating endpoint requires JWT and proves the
caller owns the agent it acts for. Ownership is checked against the agents row
on every consequential call, never inferred from an earlier check:
agents.owner_user_id is mutable and nullable, so "they owned it when they
challenged" is not "they own it now".

Turn submission (POST /battles/{id}/turns) is the one exception: it is called by
the AGENT and authenticated with its X-API-Key, deriving the fighter's side from
that identity rather than trusting the body.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, OptionalUser, get_admin_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User
from app.repositories.agent_repo import AgentRepository
from app.repositories.battle_repo import (
    MINIMUM_TASK_POOL,
    TASK_REUSE_COOLDOWN_DAYS,
    BattleRepository,
    ChallengeDenial,
)
from app.schemas.battles import (
    BattleBlockResponse,
    BattleDetail,
    BattleJudgementView,
    BattleJudgeRunView,
    BattleStatus,
    BattleSubmissionView,
    BattleSummary,
    BattleTaskPoolsResponse,
    BattleVerdictView,
    CreateBattleBlockRequest,
    CreateChallengeRequest,
    CreateDemoBattleRequest,
    CreateTaskRequest,
    JudgeKind,
    JudgeRunStatus,
    JudgeTally,
    ModerationTaskView,
    PresentedOrder,
    ReadinessView,
    RejectTaskRequest,
    Side,
    SubmitTaskRequest,
    SubmitTaskResponse,
    SubmitTurnRequest,
    TaskSource,
    TaskStatus,
    UserTaskSummary,
    Vote,
)
from app.services.agent_service import AgentService, get_agent_by_api_key
from app.services.battle_budget import current_budget_day
from app.services.battle_runner import _notify_battle_owners
from app.services.battle_service import (
    DAILY_TASK_SUBMISSION_LIMIT,
    BattleService,
    ChallengeDeniedError,
    LimiterUnavailableError,
    TaskSubmissionDenial,
    TaskSubmissionDeniedError,
    normalize_task_category,
)

# Notification task type raised on the opponent/challenger when a challenge is
# directly created or an open challenge is claimed. Terminal outcomes
# (battle_result/expired/aborted) already notify via _notify_battle_owners; this
# is the missing FIRST touch — without it a directly-challenged owner learns of
# the challenge only by browsing the arena before it expires.
_CHALLENGE_RECEIVED_TYPE = "battle_challenge_received"

router = APIRouter(prefix="/battles", tags=["battles"])

# Which HTTP status each admission rule answers with. A cap and a cooldown are
# both "not now" (429); a block or an opt-out is "not ever, by policy" (403);
# an existing engagement is a conflict with current state (409).
_DENIAL_STATUS: dict[ChallengeDenial, int] = {
    ChallengeDenial.INSUFFICIENT_TASK_POOL: 409,
    ChallengeDenial.CHALLENGER_INELIGIBLE: 403,
    ChallengeDenial.CHALLENGER_RATE_LIMITED: 429,
    ChallengeDenial.TARGET_INELIGIBLE: 403,
    ChallengeDenial.BLOCKED: 403,
    ChallengeDenial.COOLING_DOWN: 429,
    ChallengeDenial.TARGET_CAPPED: 429,
    ChallengeDenial.PAIR_ALREADY_ENGAGED: 409,
}

_DENIAL_DETAIL: dict[ChallengeDenial, str] = {
    ChallengeDenial.INSUFFICIENT_TASK_POOL: (
        "not enough fresh tasks match the requested category and difficulty"
    ),
    ChallengeDenial.CHALLENGER_INELIGIBLE: (
        "your agent is not eligible to battle: it must be active, not hosted, "
        "and opted in via available_for_battles"
    ),
    ChallengeDenial.CHALLENGER_RATE_LIMITED: (
        "your agent has reached its own hourly challenge limit"
    ),
    ChallengeDenial.TARGET_INELIGIBLE: (
        "target agent has not opted in to battles"
    ),
    ChallengeDenial.BLOCKED: "target agent has blocked this challenger",
    ChallengeDenial.COOLING_DOWN: (
        "target declined a recent challenge from this agent; cooldown active"
    ),
    ChallengeDenial.TARGET_CAPPED: (
        "target has reached its challenge limit for this window"
    ),
    ChallengeDenial.PAIR_ALREADY_ENGAGED: (
        "these agents already have a battle in progress"
    ),
}


async def _assert_owns_agent(db: AsyncSession, agent_id: str, user_id: str) -> None:
    """Prove this user owns this agent, right now, in this transaction.

    404 rather than 403 when the agent is absent, so probing for agent ids
    yields nothing. A wrong owner is a plain 403: the caller already knows the
    agent exists — they just do not own it.
    """
    row = (
        await db.execute(
            text("SELECT owner_user_id FROM agents WHERE id = CAST(:id AS UUID)"),
            {"id": str(agent_id)},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "agent not found")
    if not row["owner_user_id"] or str(row["owner_user_id"]) != str(user_id):
        raise HTTPException(403, "not your agent")


async def _optional_fighter(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict | None:
    """Identify the calling agent from its X-API-Key, or None if unauthenticated.

    Optional by design: the submissions read route is public, but a fighter that
    proves who it is may see its OWN turns while a battle runs, whereas the
    anonymous public sees no live per-turn metadata at all. A missing or unknown
    key is simply 'not a fighter here' (None), never a 401 — the route is still
    readable without one.
    """
    if not x_api_key:
        return None
    key_hash = AgentService.hash_api_key(x_api_key)
    return await AgentRepository(db).get_agent_by_api_key_hash(key_hash)


async def _notify_challenge_recipient(
    db: AsyncSession, battle_id: str, recipient_agent_id: str, title: str
) -> None:
    """Best-effort 'challenge touched you' notification, AFTER the commit.

    Reuses the exact mechanism terminal battle notifications use
    (_notify_battle_owners -> AgentService.create_notification_task), including
    its per-recipient own-transaction isolation and log-and-swallow discipline:
    the challenge is already durable by the time this runs, so a notify failure
    must never roll it back. source_key dedups on the battle id + type.
    """
    await _notify_battle_owners(
        db, battle_id, [(recipient_agent_id, _CHALLENGE_RECEIVED_TYPE, title)]
    )


# The task snapshot is PUBLIC only once a battle is actually running — the same
# gate the reveal follows everywhere (V67). Before this, the bound task (id,
# title, prompt, rubric, time limit) is withheld from every reader, so scheduler
# latency between binding at 'queued' and starting at 'running' can never be
# turned into extra preparation time. A queued battle is internally bound but
# still withheld; an aborted battle that never ran stays withheld forever.
_TASK_REVEALED = frozenset(
    {
        BattleStatus.RUNNING.value,
        BattleStatus.JUDGING.value,
        BattleStatus.COMPLETED.value,
    }
)

# The bound-task columns nulled out of a public row while the task is withheld.
# Explicitly listed, not derived: the whole point is that a reader gets a null,
# not the snapshot, before the battle runs — Pydantic dropping unknown fields is
# not a substitute for deleting the value here.
_WITHHELD_TASK_FIELDS = (
    "task_id",
    "task_title_snapshot",
    "task_prompt_snapshot",
    "task_rubric_snapshot",
    "time_limit_seconds_snapshot",
)


def _sanitize_task(battle: dict) -> tuple[dict, bool]:
    """Return a copy of ``battle`` with the task withheld unless it is running.

    Mirrors the explicit ``content_withheld`` sanitisation the submissions route
    uses: the caller builds its DTO from the returned dict and stamps
    ``task_content_withheld`` from the returned flag, so a withheld task is a
    null the reader can see is deliberately withheld, never an accidental leak.
    """
    revealed = battle["status"] in _TASK_REVEALED
    public = dict(battle)
    if not revealed:
        for field in _WITHHELD_TASK_FIELDS:
            public[field] = None
    return public, not revealed


def _readiness_view(battle: dict) -> ReadinessView:
    """Render the two readiness facts separately, never one from the other."""
    return ReadinessView(
        generation=battle["readiness_generation"],
        lease_expires_at=battle["ready_lease_expires_at"],
        accepted=battle["agent_b_accepted_at"] is not None,
        # 'queued' is the ONLY state that proves both current ready-ACKs
        # landed. Anything earlier has not proven it, and anything later
        # inherited it from the moment it was proven.
        ready=battle["status"]
        in (
            BattleStatus.QUEUED.value,
            BattleStatus.RUNNING.value,
            BattleStatus.JUDGING.value,
            BattleStatus.COMPLETED.value,
        ),
    )


@router.get("", response_model=list[BattleSummary], summary="List battles")
async def list_battles(
    status: BattleStatus | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Public battle list, newest first.

    Every row is sanitised through the same reveal-status gate the detail route
    uses (V67): a bound-but-queued battle must not leak its task id or title
    before it runs, so the list cannot become a pre-fetch side channel either.
    """
    rows = await BattleRepository(db).list_battles(
        status=status, limit=limit, offset=offset
    )
    summaries: list[BattleSummary] = []
    for row in rows:
        public, withheld = _sanitize_task(row)
        summaries.append(BattleSummary(**public, task_content_withheld=withheld))
    return summaries


@router.get(
    "/tasks",
    response_model=BattleTaskPoolsResponse,
    summary="Task pool availability for new challenges",
)
async def list_task_pools(db: AsyncSession = Depends(get_db)):
    """Public pool aggregates — counts per (category, difficulty), no content.

    Replaces the V66 catalog, which returned whole task rows (id, title, prompt,
    rubric) and so let a rated challenger read the exact tasks before binding.
    This returns only how many FRESH tasks each filter bucket holds and whether
    it clears the minimum-pool gate, which is all the UI needs to offer and
    disable filter choices — and reveals no task a challenger could precompute.
    """
    pools = await BattleRepository(db).list_task_pools()
    return BattleTaskPoolsResponse(
        minimum_pool_size=MINIMUM_TASK_POOL,
        cooldown_days=TASK_REUSE_COOLDOWN_DAYS,
        pools=pools,
    )


@router.post("/tasks/generate", status_code=201, summary="Create a battle task")
async def generate_task(
    body: CreateTaskRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: mint a task fighters can be challenged over.

    Category is normalised the same way the challenge filter is, so a task
    created as "Backend" buckets with a challenge filtered on "backend".
    """
    category = normalize_task_category(body.category)
    if category is None:
        raise HTTPException(422, "category must not be blank")
    task_id = await BattleRepository(db).create_task(
        source=TaskSource.GENERATED,
        title=body.title,
        prompt=body.prompt,
        rubric=body.rubric,
        time_limit_seconds=body.time_limit_seconds,
        category=category,
        difficulty=body.difficulty.value,
        created_by_user_id=str(admin.id),
    )
    await db.commit()
    return {"id": task_id}


# --- User task submission (V70) --------------------------------------------
# These STATIC /tasks/* routes are declared before GET /{battle_id} for the same
# reason /blocks is: FastAPI matches in declaration order, and "tasks" would
# otherwise be read as a battle id.

_SUBMISSION_DENIAL_STATUS: dict[TaskSubmissionDenial, int] = {
    TaskSubmissionDenial.DAILY_QUOTA_EXHAUSTED: 429,
    TaskSubmissionDenial.DUPLICATE_CONTENT: 409,
}

_SUBMISSION_DENIAL_DETAIL: dict[TaskSubmissionDenial, str] = {
    TaskSubmissionDenial.DAILY_QUOTA_EXHAUSTED: (
        f"daily submission limit reached ({DAILY_TASK_SUBMISSION_LIMIT} per day)"
    ),
    TaskSubmissionDenial.DUPLICATE_CONTENT: (
        "an identical task was submitted at the same moment"
    ),
}


@router.post("/tasks", response_model=SubmitTaskResponse, status_code=201,
             summary="Submit a battle task")
async def submit_task(
    body: SubmitTaskRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Any registered user may propose a task. It does NOT enter the rated pool.

    An accepted submission is quarantined: it is played only in battles that
    cannot move Elo, until a moderator approves it. That is what makes the
    submitter's own knowledge of their task worthless, and it is why 201 here
    does not mean "in the pool".

    A 201 with ``status='rejected'`` is a real outcome, not an error: the cheap
    filters refused it and the row exists so the author can read the reason.
    """
    category = normalize_task_category(body.category)
    if category is None:
        raise HTTPException(422, "category must not be blank")
    try:
        outcome = await BattleService(db).submit_task(
            user_id=str(user.id),
            title=body.title,
            prompt=body.prompt,
            rubric=body.rubric,
            category=category,
            difficulty=body.difficulty.value,
            time_limit_seconds=body.time_limit_seconds,
        )
    except TaskSubmissionDeniedError as denied:
        raise HTTPException(
            _SUBMISSION_DENIAL_STATUS[denied.reason],
            _SUBMISSION_DENIAL_DETAIL[denied.reason],
        ) from denied
    return SubmitTaskResponse(**outcome)


@router.get("/tasks/mine", response_model=list[UserTaskSummary],
            summary="My submitted tasks")
async def list_my_tasks(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """The caller's own submissions with their status and rejection reason."""
    rows = await BattleRepository(db).list_submissions_by_author(str(user.id))
    return [UserTaskSummary(**row) for row in rows]


@router.get("/tasks/moderation", response_model=list[ModerationTaskView],
            summary="Moderation queue")
async def list_task_moderation_queue(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: submissions awaiting validation or approval, oldest first.

    Carries each task's quarantine record, because approving one lets it move
    real Elo and the record is the only evidence a moderator has that the author
    is not playing their own task through an accomplice.
    """
    rows = await BattleRepository(db).list_moderation_queue()
    return [
        ModerationTaskView(
            author_user_id=row.pop("created_by_user_id"),
            **row,
        )
        for row in rows
    ]


@router.post("/tasks/{task_id}/approve", summary="Approve a quarantined task")
async def approve_task(
    task_id: UUID,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: quarantine -> ready, the ONLY route into the rated pool.

    409, not 404, when the task is not in quarantine: it usually exists and was
    already rejected or already approved, and answering "not found" would send a
    moderator looking for a row that is right there.
    """
    if not await BattleService(db).approve_task(str(task_id), str(admin.id)):
        raise HTTPException(409, "task is not awaiting approval")
    return {"id": str(task_id), "status": TaskStatus.READY.value}


@router.post("/tasks/{task_id}/reject", summary="Reject a submitted task")
async def reject_task(
    task_id: UUID,
    body: RejectTaskRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: refuse a pending or quarantined submission, with a reason."""
    if not await BattleService(db).reject_task(str(task_id), body.reason):
        raise HTTPException(409, "task is not awaiting validation or approval")
    return {"id": str(task_id), "status": TaskStatus.REJECTED.value}


# --- Owner-level blocks (V68 D) --------------------------------------------
# These STATIC routes MUST be declared before GET /{battle_id}: FastAPI matches
# in declaration order, so "/blocks" placed after the parametrised route would
# be swallowed as a battle id.


@router.get("/blocks", response_model=list[BattleBlockResponse], summary="List my blocks")
async def list_battle_blocks(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Every owner this caller has blocked. Private to the caller."""
    rows = await BattleRepository(db).list_blocks(str(user.id))
    return [
        BattleBlockResponse(
            id=r["id"],
            blocked_owner_id=r["blocked_owner_user_id"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/blocks", response_model=BattleBlockResponse, status_code=201,
             summary="Block an owner from battling you")
async def create_battle_block(
    body: CreateBattleBlockRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Block another owner. Covers all of their current and future agents.

    ``blocked_agent_id`` is resolved to the agent's current owner; a missing or
    ownerless agent is a 404. A self-block is a 422. Idempotent: re-blocking the
    same owner returns the existing row.
    """
    if body.blocked_agent_id is not None:
        target_owner = await AgentRepository(db).get_agent_owner_user_id(
            str(body.blocked_agent_id)
        )
        if target_owner is None:
            raise HTTPException(404, "agent not found")
        target_owner = str(target_owner)
    else:
        target_owner = str(body.blocked_owner_id)
        # Validate the owner exists so a bad id is a clean 404, not an unhandled
        # FK IntegrityError 500 (FK). Mirrors the blocked_agent_id 404 branch.
        if not await BattleRepository(db).owner_exists(target_owner):
            raise HTTPException(404, "owner not found")

    if target_owner == str(user.id):
        raise HTTPException(422, "cannot block yourself")

    row = await BattleRepository(db).create_block(str(user.id), target_owner)
    await db.commit()
    return BattleBlockResponse(
        id=row["id"],
        blocked_owner_id=row["blocked_owner_user_id"],
        created_at=row["created_at"],
    )


@router.delete("/blocks/{block_id}", status_code=204, summary="Remove a block")
async def delete_battle_block(
    block_id: str,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Unblock. 404 for an absent block or one belonging to another owner."""
    deleted = await BattleRepository(db).delete_block(block_id, str(user.id))
    if not deleted:
        raise HTTPException(404, "block not found")
    await db.commit()


@router.get("/{battle_id}", response_model=BattleDetail, summary="Get one battle")
async def get_battle(
    battle_id: str,
    viewer: OptionalUser,
    db: AsyncSession = Depends(get_db),
):
    """Public battle detail.

    Judge verdicts are withheld until 'completed': revealing a running
    battle's votes would let a fighter still mid-answer read the scoring.

    The owner-snapshot UUIDs are NOT shipped (they would leak the ownership
    graph). ``viewer_can_accept`` is computed here from the optional JWT using
    the SAME predicate the accept CAS enforces (repo.can_accept), so the
    advertised capability matches what accepting would actually do — not the
    frozen snapshot, which would show the button to a former owner whose accept
    can no longer succeed.
    """
    repo = BattleRepository(db)
    battle = await repo.get(battle_id)
    if battle is None:
        raise HTTPException(404, "battle not found")
    # Withhold the bound task until the battle is running (V67). The nulling is
    # explicit here, before the DTO is built — not left to Pydantic — so a
    # queued battle that is already internally bound still returns a null task
    # and prompt, and ``task_content_withheld`` says so.
    public, withheld = _sanitize_task(battle)
    detail = BattleDetail(
        **public,
        task_content_withheld=withheld,
        readiness=_readiness_view(battle),
    )
    if battle["status"] != BattleStatus.COMPLETED.value:
        detail.verdict_reason = None
    if viewer is not None and battle["status"] == BattleStatus.CHALLENGE_PENDING.value:
        detail.viewer_can_accept = await repo.can_accept(battle_id, str(viewer.id))
    return detail


@router.post("", status_code=201, summary="Challenge an agent to a battle")
async def create_challenge(
    body: CreateChallengeRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Open a challenge on behalf of an agent the caller owns.

    ``agent_b_id`` omitted opens the challenge to any eligible claimant.
    A denial creates no battle row — the admission rules are predicates of the
    INSERT itself, not a check performed beforehand.
    """
    await _assert_owns_agent(db, str(body.agent_a_id), str(user.id))
    svc = BattleService(db)
    try:
        battle_id = await svc.create_challenge(
            task_category=body.task_category,
            task_difficulty=body.task_difficulty.value if body.task_difficulty else None,
            agent_a_id=str(body.agent_a_id),
            challenger_owner_user_id=str(user.id),
            agent_b_id=str(body.agent_b_id) if body.agent_b_id else None,
        )
    except ChallengeDeniedError as denied:
        await db.rollback()
        raise HTTPException(
            _DENIAL_STATUS[denied.reason], _DENIAL_DETAIL[denied.reason]
        ) from denied
    except LimiterUnavailableError as exc:
        await db.rollback()
        # Fail closed. A challenge spends the target owner's inference budget,
        # so a limiter we cannot consult must deny rather than wave it through.
        raise HTTPException(
            503, "challenge limiter unavailable; try again shortly"
        ) from exc
    await db.commit()

    # A NAMED challenge touches a specific opponent — notify its owner now, so a
    # directly-challenged owner does not have to browse the arena to discover it.
    # Best-effort, strictly AFTER the commit: the challenge is durable and must
    # not be rolled back by a notify failure. An OPEN challenge (no agent_b_id)
    # has no opponent yet, so there is nobody to notify until it is claimed.
    if body.agent_b_id:
        await _notify_challenge_recipient(
            db,
            battle_id,
            str(body.agent_b_id),
            f"Новый вызов на бой (бой {battle_id})",
        )
    return {"id": battle_id}


@router.post("/demo", status_code=201, summary="Battle the platform demo opponent")
async def create_demo_battle(
    body: CreateDemoBattleRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Pit an agent the caller owns against the platform demo opponent — UNRATED.

    A demo battle needs ZERO human action on the demo side: the demo opponent is
    consented for INLINE here (so the battle is returned already 'accepted', with
    no ~30s wait and no 'challenge_pending' pressure on TARGET_CHALLENGE_CAP), then
    the reconciler auto-ACKs readiness and auto-submits a live answer. The battle
    is created ``is_demo``, so ``BattleService._decide_rated_eligibility``
    suppresses rating (reason 'demo') and no Elo can ever move — the ordinary
    challenge/create path is reused with the demo agent as the opponent, not a
    parallel lifecycle. The reconciler's auto-accept remains the crash backstop.

    503 when no demo opponent is configured (no admin existed when the migration
    seeded, so no sparring agent). Every admission denial the normal challenge
    raises (ineligible fighter, exhausted task pool, rate limit, pair already
    engaged) is reported with its usual status.
    """
    await _assert_owns_agent(db, str(body.agent_a_id), str(user.id))
    demo_agent_id = await BattleRepository(db).get_demo_opponent()
    if demo_agent_id is None:
        raise HTTPException(503, "no demo opponent is configured")
    svc = BattleService(db)
    try:
        battle_id = await svc.create_demo_battle(
            agent_a_id=str(body.agent_a_id),
            challenger_owner_user_id=str(user.id),
            demo_agent_id=demo_agent_id,
            task_category=body.task_category,
            task_difficulty=body.task_difficulty.value if body.task_difficulty else None,
        )
    except ChallengeDeniedError as denied:
        await db.rollback()
        raise HTTPException(
            _DENIAL_STATUS[denied.reason], _DENIAL_DETAIL[denied.reason]
        ) from denied
    except LimiterUnavailableError as exc:
        await db.rollback()
        raise HTTPException(
            503, "challenge limiter unavailable; try again shortly"
        ) from exc
    await db.commit()
    return {"id": battle_id}


class ClaimChallengeRequest(BaseModel):
    """Which of the caller's agents is stepping into an open challenge."""

    agent_id: UUID


@router.post("/{battle_id}/claim", summary="Claim an open challenge")
async def claim_open_challenge(
    battle_id: str,
    body: ClaimChallengeRequest,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Step into an open challenge with an agent the caller owns.

    The claimant passes exactly the rules a named opponent passes — opt-in,
    activation, not-hosted, ownership, blocks in both directions, cooldown and
    the per-target cap — because an open challenge would otherwise be the way
    around all of them: challenge nobody, and wait for the agent you blocked.

    Claiming is not consent. The battle stays pending and the claimant's owner
    must still accept, which is the same two-step a named opponent goes
    through.

    A refusal is a single 409 whatever the reason. Naming the rule would let a
    claimant read someone else's block list one probe at a time.
    """
    await _assert_owns_agent(db, str(body.agent_id), str(user.id))
    claimed = await BattleService(db).claim_open_challenge(
        battle_id=battle_id,
        agent_b_id=str(body.agent_id),
        claiming_user_id=str(user.id),
    )
    if claimed is None:
        await db.rollback()
        raise HTTPException(
            409,
            "cannot claim: the challenge is gone, already taken, expired, or "
            "your agent is not eligible for it",
        )
    await db.commit()

    # The open challenge now has a taker — tell the CHALLENGER (agent_a), whose
    # open challenge was silently waiting, that an opponent stepped in. The
    # claimant is the caller and needs no notice. Best-effort, after the commit,
    # for the same reason as the named-challenge notification above.
    await _notify_challenge_recipient(
        db,
        str(claimed["id"]),
        str(claimed["agent_a_id"]),
        f"Твой открытый вызов принят (бой {claimed['id']})",
    )
    return {"id": str(claimed["id"]), "status": claimed["status"]}


@router.post("/{battle_id}/accept", summary="Accept a challenge (B's owner)")
async def accept_challenge(
    battle_id: str,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Record owner consent for the opponent's side.

    Consent ONLY. It does not require the agent to be online, reachable, or
    connected — a live handoff at this moment would say nothing about liveness
    at start time, which is why readiness is proven separately, later, and
    against the ACKs.
    """
    repo = BattleRepository(db)
    battle = await repo.get(battle_id)
    if battle is None:
        raise HTTPException(404, "battle not found")
    if battle["agent_b_id"] is None:
        raise HTTPException(409, "open challenge has no opponent yet")
    # Shapes the error message only. It is NOT the ownership check: sessions run
    # READ COMMITTED, so an owner change committed between this read and the
    # write would sail straight through it. The check that counts is the
    # owner predicate inside accept_as_owner's CAS.
    await _assert_owns_agent(db, str(battle["agent_b_id"]), str(user.id))

    # Advisory budget preflight (B4): only a distinct-owner battle can become
    # rated and so consume the judge budget. If the day's budget could not judge
    # it (fewer than a panel's worth of units left — six per battle — for the
    # global pool or either owner), refuse acceptance with 429 rather than
    # accepting a rated battle that would immediately settle unrated at judging.
    # The per-call reservation transaction remains the authoritative arbiter.
    owner_a = battle.get("agent_a_owner_snapshot")
    owner_b = battle.get("agent_b_owner_snapshot")
    if owner_a is not None and owner_b is not None and str(owner_a) != str(owner_b):
        settings = get_settings()
        global_used, owner_used = await repo.judge_budget_usage(
            [str(owner_a), str(owner_b)], current_budget_day()
        )
        if (
            settings.battle_judge_global_daily_call_limit - global_used < 6
            or settings.battle_judge_owner_daily_call_limit - owner_used < 6
        ):
            raise HTTPException(
                429,
                "rated judging budget is exhausted; try again after the daily reset",
            )

    accepted = await BattleService(db).accept(battle_id, str(user.id))
    if accepted is None:
        raise HTTPException(
            409,
            "challenge is no longer pending, has expired, or the agent's "
            "ownership or eligibility changed",
        )
    await db.commit()
    return {"status": accepted["status"]}


@router.post("/{battle_id}/decline", summary="Decline a challenge (B's owner)")
async def decline_challenge(
    battle_id: str,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Refuse a challenge and start this challenger's cooldown on this target."""
    repo = BattleRepository(db)
    battle = await repo.get(battle_id)
    if battle is None:
        raise HTTPException(404, "battle not found")
    if battle["agent_b_id"] is None:
        raise HTTPException(409, "open challenge has no opponent yet")
    await _assert_owns_agent(db, str(battle["agent_b_id"]), str(user.id))

    declined = await BattleService(db).decline(battle_id, str(user.id))
    if declined is None:
        raise HTTPException(
            409,
            "challenge is no longer pending, or the agent's ownership changed",
        )
    await db.commit()
    return {"status": declined["status"]}


@router.post("/{battle_id}/turns")
async def submit_turn(
    battle_id: str,
    body: SubmitTurnRequest,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """A fighter posts a checkpoint or its final answer.

    Authenticated by the AGENT's X-API-Key, not the owner's JWT: this is the one
    battle endpoint the agent itself calls, and the key proves which fighter is
    speaking. The side is DERIVED from that identity — never taken from the body
    — so an agent cannot submit as its opponent.

    Every rejection here is a rule the judges would otherwise score a lie:

    * not a fighter in this battle -> 403, read from the battle row.
    * battle not 'running' -> 409. Before the shared start there is nothing to
      answer; once judging begins the answers are frozen.
    * past ``deadline_at`` -> 409, timed by the SERVER's clock against the column
      the transition statement computed. The request carries no timestamp to
      argue with.
    * a taken ``seq_no``, or a second final -> 409, arbitrated by the primary key
      and the partial unique index rather than by a prior read.

    Finality is one-way and idempotent: once a side is final the reconciler may
    judge at any moment, so a later turn must not change the answer under it.
    """
    repo = BattleRepository(db)
    battle = await repo.get(battle_id)
    if battle is None:
        raise HTTPException(404, "battle not found")

    agent_id = str(agent["id"])
    if agent_id == str(battle["agent_a_id"]):
        side = Side.A
    elif battle["agent_b_id"] and agent_id == str(battle["agent_b_id"]):
        side = Side.B
    else:
        raise HTTPException(403, "your agent is not a fighter in this battle")

    if battle["status"] != BattleStatus.RUNNING.value:
        raise HTTPException(409, f"battle is not accepting turns in status '{battle['status']}'")

    # The server's clock decides, and it is the only clock in the room.
    deadline = battle["deadline_at"]
    if deadline is None or deadline <= datetime.now(UTC):
        raise HTTPException(409, "the deadline has passed")

    accepted = await repo.add_submission(
        battle_id=battle_id,
        side=side,
        seq_no=body.seq_no,
        content=body.content,
        is_final=body.is_final,
        tokens_used=body.tokens_used,
    )
    if not accepted:
        # The database arbitrated: this seq_no is taken, or this side already
        # said its last word (possibly the reconciler's synthetic one).
        raise HTTPException(409, "this turn slot is already taken, or your side is already final")

    # Persist the fighter's answer FIRST and on its own — nothing below may put
    # this commit at risk.
    await db.commit()

    if body.is_final:
        # Speed-up only, in a SEPARATE transaction after the final is durable:
        # if both sides are now final, retire the running row's lease so the
        # reconciler judges on its next tick instead of waiting out the whole
        # BATTLE_LEASE_SECONDS window. Post-commit, so both finals are visible
        # and whichever side commits last is the one that fires the release —
        # the READ COMMITTED race that an in-transaction call would have lost.
        # Best-effort: a failure costs only the speed-up (the reconciler still
        # judges at the deadline), never the already-persisted final.
        try:
            await repo.expire_running_lease_if_both_final(battle_id)
            await db.commit()
        except Exception as exc:
            logger.warning("battle {} early-finish lease release failed: {}", battle_id, exc)
            await db.rollback()

    return {
        "status": "accepted",
        "side": side.value,
        "seq_no": body.seq_no,
        "is_final": body.is_final,
    }

# Statuses in which a battle no longer accepts turns, so a submission's content
# can no longer help the opponent. Judging and completed only — NOT running.
_TURNS_CLOSED = frozenset({BattleStatus.JUDGING.value, BattleStatus.COMPLETED.value})


@router.get(
    "/{battle_id}/submissions",
    response_model=list[BattleSubmissionView],
    summary="List a battle's submissions",
)
async def list_battle_submissions(
    battle_id: str,
    fighter: dict | None = Depends(_optional_fighter),
    db: AsyncSession = Depends(get_db),
):
    """Metadata once turns close; while RUNNING, only your OWN side's metadata.

    Two different leaks are closed here, and they need different rules:

    ``content`` is withheld from EVERYONE until the turns are closed — handing B
    the text of A's checkpoint mid-battle would turn this endpoint into the
    cheating tool. That includes the owners (who can relay it) and A itself
    (which gains nothing from reading its own text back). ``content_withheld``
    says so rather than presenting an empty answer as if nothing was written.

    Per-turn METADATA — side, seq_no, is_final, truncated, error, received_at,
    tokens_used — is a subtler leak while the battle is still RUNNING. Showing a
    fighter the OPPONENT's rows reveals that the opponent already went final and
    how many checkpoints/tokens it spent, so a fighter can poll, see the other
    side commit, and safely hold its own final for a last-mover advantage. So
    while running:

    * an authenticated fighter (X-API-Key) sees ONLY its own side's turns;
    * the anonymous public / a non-fighter sees NEITHER side's live metadata —
      an empty list, which leaks nothing exploitable.

    Once the battle is judging or completed the answers are frozen and every
    submission (metadata AND content) is public: the whole premise is that
    spectators can read what was written and judge the judges.
    """
    repo = BattleRepository(db)
    battle = await repo.get(battle_id)
    if battle is None:
        raise HTTPException(404, "battle not found")

    turns_closed = battle["status"] in _TURNS_CLOSED

    # While turns are still open, restrict per-turn metadata to the requesting
    # fighter's own side. A non-fighter viewer (fighter is None, or its id is
    # neither side) sees nothing live — no row's metadata escapes.
    viewer_side: Side | None = None
    if not turns_closed and fighter is not None:
        fighter_id = str(fighter["id"])
        if fighter_id == str(battle["agent_a_id"]):
            viewer_side = Side.A
        elif battle["agent_b_id"] and fighter_id == str(battle["agent_b_id"]):
            viewer_side = Side.B

    return [
        BattleSubmissionView(
            side=Side(str(row["side"])),
            seq_no=row["seq_no"],
            is_final=row["is_final"],
            truncated=row["truncated"],
            error=row["error"],
            received_at=row["received_at"],
            tokens_used=row["tokens_used"],
            content=row["content"] if turns_closed else None,
            content_withheld=not turns_closed,
        )
        for row in await repo.list_submissions(battle_id)
        if turns_closed or (viewer_side is not None and str(row["side"]) == viewer_side.value)
    ]


@router.get(
    "/{battle_id}/judgements",
    response_model=BattleVerdictView,
    summary="A completed battle's verdict, with its evidence",
)
async def get_battle_verdict(battle_id: str, db: AsyncSession = Depends(get_db)):
    """Public, but ONLY once completed. Before that: empty, for everyone.

    This is the same rule GET /battles/{id} applies to verdict_reason, and it is
    not cosmetic: a fighter who can watch itself being scored mid-battle can
    steer its remaining answer at the rubric the judge is rewarding, and an owner
    can relay that. So the gate is on STATUS, not on identity — there is no
    caller privileged enough to see a verdict that does not exist yet.

    An unfinished battle returns empty collections rather than 403: the absence
    of a verdict is public information, and a 403 would imply there is something
    to hide from THIS caller specifically, which is not the shape of the rule.

    What is returned is deliberately more than the answer. Collapsed judgements
    give the arithmetic; the RAW runs give the evidence for it — two rows per
    replicate seed, one 'ab' and one 'ba', which is what makes the position-bias
    control checkable rather than merely claimed. Tallies are split per judge
    kind because LLM replicates and humans do not share a quorum: three
    correlated samples of one model are not three judges.
    """
    repo = BattleRepository(db)
    battle = await repo.get(battle_id)
    if battle is None:
        raise HTTPException(404, "battle not found")

    if battle["status"] != BattleStatus.COMPLETED.value:
        return BattleVerdictView(judgements=[], runs=[], tallies={})

    judgements = [
        BattleJudgementView(
            judge_kind=JudgeKind(str(row["judge_kind"])),
            judge_ref=row["judge_ref"],
            replicate_seed=row["replicate_seed"],
            vote=Vote(str(row["vote"])),
            confidence=row["confidence"],
            reasoning=row["reasoning"],
            scores=row["scores"],
            position_sensitive=row["position_sensitive"],
        )
        for row in await repo.list_judgements(battle_id)
    ]
    runs = [
        BattleJudgeRunView(
            judge_kind=JudgeKind(str(row["judge_kind"])),
            judge_ref=row["judge_ref"],
            replicate_seed=row["replicate_seed"],
            presented_order=PresentedOrder(str(row["presented_order"])),
            status=JudgeRunStatus(str(row["status"])),
            vote=Vote(str(row["vote"])) if row["vote"] else None,
            confidence=row["confidence"],
            reasoning=row["reasoning"],
            scores=row["scores"],
        )
        for row in await repo.list_judge_runs(battle_id)
    ]
    return BattleVerdictView(
        judgements=judgements, runs=runs, tallies=_tally_by_kind(judgements)
    )


def _tally_by_kind(judgements: list[BattleJudgementView]) -> dict[str, JudgeTally]:
    """Count each judge kind's votes separately.

    Abstentions and errors are counted but excluded from ``valid`` — the quorum
    denominator — mirroring the resolution in battle_judges.resolve_verdict. A
    tally that folded them in would let a panel of three errors look unanimous.
    """
    tallies: dict[str, JudgeTally] = {}
    for judgement in judgements:
        tally = tallies.setdefault(judgement.judge_kind.value, JudgeTally())
        if judgement.vote is Vote.A:
            tally.votes_for_a += 1
            tally.valid += 1
        elif judgement.vote is Vote.B:
            tally.votes_for_b += 1
            tally.valid += 1
        elif judgement.vote is Vote.TIE:
            tally.ties += 1
            tally.valid += 1
        elif judgement.vote is Vote.ABSTAIN:
            tally.abstained += 1
        else:
            tally.errored += 1
        if judgement.position_sensitive:
            tally.position_sensitive += 1
    return tallies

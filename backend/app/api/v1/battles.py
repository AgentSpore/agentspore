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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_admin_user
from app.core.database import get_db
from app.models.user import User
from app.repositories.battle_repo import BattleRepository, ChallengeDenial
from app.schemas.battles import (
    BattleDetail,
    BattleJudgementView,
    BattleJudgeRunView,
    BattleStatus,
    BattleSubmissionView,
    BattleSummary,
    BattleVerdictView,
    CreateChallengeRequest,
    CreateTaskRequest,
    JudgeKind,
    JudgeRunStatus,
    JudgeTally,
    PresentedOrder,
    ReadinessView,
    Side,
    SubmitTurnRequest,
    TaskSource,
    Vote,
)
from app.services.agent_service import get_agent_by_api_key
from app.services.battle_service import (
    BattleService,
    ChallengeDeniedError,
    LimiterUnavailableError,
)

router = APIRouter(prefix="/battles", tags=["battles"])

# Which HTTP status each admission rule answers with. A cap and a cooldown are
# both "not now" (429); a block or an opt-out is "not ever, by policy" (403);
# an existing engagement is a conflict with current state (409).
_DENIAL_STATUS: dict[ChallengeDenial, int] = {
    ChallengeDenial.TASK_UNAVAILABLE: 404,
    ChallengeDenial.CHALLENGER_INELIGIBLE: 403,
    ChallengeDenial.CHALLENGER_RATE_LIMITED: 429,
    ChallengeDenial.TARGET_INELIGIBLE: 403,
    ChallengeDenial.BLOCKED: 403,
    ChallengeDenial.COOLING_DOWN: 429,
    ChallengeDenial.TARGET_CAPPED: 429,
    ChallengeDenial.PAIR_ALREADY_ENGAGED: 409,
}

_DENIAL_DETAIL: dict[ChallengeDenial, str] = {
    ChallengeDenial.TASK_UNAVAILABLE: "task not found or not ready",
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
    """Public battle list, newest first."""
    rows = await BattleRepository(db).list_battles(
        status=status, limit=limit, offset=offset
    )
    return [BattleSummary(**row) for row in rows]


@router.get("/tasks", summary="List battle tasks available for new challenges")
async def list_tasks(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Public list of 'ready' tasks."""
    return await BattleRepository(db).list_tasks(limit=limit, offset=offset)


@router.post("/tasks/generate", status_code=201, summary="Create a battle task")
async def generate_task(
    body: CreateTaskRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: mint a task fighters can be challenged over."""
    task_id = await BattleRepository(db).create_task(
        source=TaskSource.GENERATED,
        title=body.title,
        prompt=body.prompt,
        rubric=body.rubric,
        time_limit_seconds=body.time_limit_seconds,
        category=body.category,
        created_by_user_id=str(admin.id),
    )
    await db.commit()
    return {"id": task_id}


@router.get("/{battle_id}", response_model=BattleDetail, summary="Get one battle")
async def get_battle(battle_id: str, db: AsyncSession = Depends(get_db)):
    """Public battle detail.

    Judge verdicts are withheld until 'completed': revealing a running
    battle's votes would let a fighter still mid-answer read the scoring.
    """
    battle = await BattleRepository(db).get(battle_id)
    if battle is None:
        raise HTTPException(404, "battle not found")
    detail = BattleDetail(**battle, readiness=_readiness_view(battle))
    if battle["status"] != BattleStatus.COMPLETED.value:
        detail.verdict_reason = None
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
            task_id=str(body.task_id),
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

    if body.is_final:
        # Both sides now final: retire the running row's lease so the reconciler
        # claims and judges this battle on its next tick instead of waiting out
        # the whole BATTLE_LEASE_SECONDS window. The two-final count is a CAS
        # inside the statement, so this is a no-op until the second final lands.
        await repo.expire_running_lease_if_both_final(battle_id)

    await db.commit()
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
async def list_battle_submissions(battle_id: str, db: AsyncSession = Depends(get_db)):
    """Public. Metadata always; CONTENT only once the battle stops taking turns.

    The split is the whole point. A spectator screen needs to say "submitted /
    timed out / never answered", and that is metadata — side, seq_no, is_final,
    truncated, error. None of it helps a fighter.

    ``content`` is different. While the battle is RUNNING, handing B the text of
    A's checkpoint would turn this endpoint into the cheating tool: B is still
    writing, and A's answer is exactly what B wants. So content is withheld from
    EVERYONE until the turns are closed — including the owners, because an owner
    can relay it to their agent, and including A, because A gains nothing from
    reading its own text back. ``content_withheld`` says so explicitly rather
    than presenting an empty answer as if the fighter had written nothing.

    Once the battle is judging or completed the answers are frozen and public:
    the whole premise is that spectators can read what was written and judge the
    judges.
    """
    repo = BattleRepository(db)
    battle = await repo.get(battle_id)
    if battle is None:
        raise HTTPException(404, "battle not found")

    turns_closed = battle["status"] in _TURNS_CLOSED
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

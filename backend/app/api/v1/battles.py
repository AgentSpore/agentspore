"""Battles API — challenge, consent, reservation and readiness (step 8).

Read endpoints are public; every mutating endpoint requires JWT and proves the
caller owns the agent it acts for. Ownership is checked against the agents row
on every consequential call, never inferred from an earlier check:
agents.owner_user_id is mutable and nullable, so "they owned it when they
challenged" is not "they own it now".

Turn submission (POST /battles/{id}/turns, X-API-Key) belongs to step 9 and is
deliberately absent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_admin_user
from app.core.database import get_db
from app.models.user import User
from app.repositories.battle_repo import BattleRepository, ChallengeDenial
from app.schemas.battles import (
    BattleDetail,
    BattleStatus,
    BattleSummary,
    CreateChallengeRequest,
    CreateTaskRequest,
    ReadinessView,
    TaskSource,
)
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

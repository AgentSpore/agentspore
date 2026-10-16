"""Battle deadlines use DB time; abort timestamps retain their persisted lower bound."""

from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
import test_battle_runner as baseline
from redis.asyncio import Redis
from sqlalchemy import text
from test_battle_runner import db_session as db_session
from test_battle_runner import engine as engine
from test_battle_runner import pg_container as pg_container
from test_battle_runner import session_maker as session_maker
from test_battle_runner import task_id as task_id

from app.repositories.battle_repo import BattleRepository
from app.services.battle_runner import BattleRunner
from app.services.battle_service import BattleService
from app.services.llm_gate import LLMGate

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest.mark.parametrize("expired,host_hours", [(True, -24), (False, 24)])
async def test_running_deadline_ignores_host_clock_offset(
    session_maker, db_session, task_id, expired, host_hours
):
    """A clock behind cannot delay silence; a clock ahead cannot invent it early."""
    battle_id, _, _, token = await baseline._battle_running(db_session, task_id)
    if expired:
        await db_session.execute(
            text(
                "UPDATE battles SET challenged_at = NOW() - INTERVAL '30 minutes', "
                "queued_at = NOW() - INTERVAL '20 minutes', "
                "started_at = NOW() - INTERVAL '10 minutes', "
                "deadline_at = NOW() - INTERVAL '1 second' WHERE id = CAST(:b AS UUID)"
            ),
            {"b": battle_id},
        )
    host_now = await db_session.scalar(
        text("SELECT NOW() + make_interval(hours => :hours)"), {"hours": host_hours}
    )
    await db_session.commit()
    clock = Mock()
    clock.now.return_value = host_now
    with patch("app.services.battle_runner.datetime", clock, create=True):
        async with session_maker() as session, Redis(host="127.0.0.1", port=1) as redis_client:
            closed = await BattleRunner(session, gate=LLMGate(redis_client)).close_deadline(
                battle_id, token
            )
    assert closed is expired
    async with session_maker() as session:
        repo = BattleRepository(session)
        battle = await repo.get(battle_id)
        assert battle is not None
        finals = [row for row in await repo.list_submissions(battle_id) if row["is_final"]]
    assert battle["status"] == ("judging" if expired else "running")
    assert len(finals) == (2 if expired else 0)
    if not expired:
        assert battle["lease_token"] is None
        assert battle["lease_attempt_count"] == 0


@pytest.mark.parametrize("expired,host_hours", [(True, -24), (False, 24)])
async def test_readiness_expiry_ignores_host_clock_offset(
    session_maker, db_session, task_id, expired, host_hours
):
    """Only a DB-expired readiness lease releases fighters, regardless of host time."""
    battle_id, _, _ = await baseline.TestReadinessGriefIsBounded()._accepted_battle(
        db_session, task_id
    )
    service = BattleService(db_session)
    armed = await service.arm_readiness(battle_id)
    assert armed is not None
    await db_session.commit()
    if expired:
        await baseline._lapse_ready_lease(db_session, battle_id)
    host_now = await db_session.scalar(
        text("SELECT NOW() + make_interval(hours => :hours)"), {"hours": host_hours}
    )
    await db_session.commit()
    clock = Mock()
    clock.now.return_value = host_now
    with patch("app.services.battle_service.datetime", clock, create=True):
        outcome = await service.expire_or_abort_readiness(battle_id)
    await db_session.commit()
    battle = await service.repo.get(battle_id)
    assert battle is not None
    assert battle["readiness_generation"] == armed["readiness_generation"]
    assert battle["status"] == ("accepted" if expired else "reserved")
    if expired:
        assert outcome is not None and outcome["outcome"] == "released"
    else:
        assert outcome is None
    assert await baseline._reservation_count(session_maker, battle_id) == (0 if expired else 2)


@pytest_asyncio.fixture(
    params=["mark_aborted", "abort_unready_readiness", "abort_pool_exhausted"],
    loop_scope="module",
)
async def abort_candidate(request, session_maker, db_session, task_id):
    """Reach each real abort seam, then simulate a clock behind the saved challenge."""
    writer = request.param
    builder = baseline.TestReadinessGriefIsBounded()
    battle_id, _, _ = await builder._accepted_battle(db_session, task_id)
    service = BattleService(db_session)
    args = ("clock regression",)
    if writer != "mark_aborted":
        armed = await service.arm_readiness(battle_id)
        assert armed is not None
        await db_session.commit()
        if writer == "abort_unready_readiness":
            await db_session.execute(
                text(
                    "UPDATE battles SET readiness_generation = :cap, "
                    "ready_lease_expires_at = NOW() - INTERVAL '1 second' "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"cap": baseline.READY_MAX_GENERATIONS, "b": battle_id},
            )
            args = (baseline.READY_MAX_GENERATIONS, "clock regression")
        else:
            for side in ("a", "b"):
                await service.events.mark_acked(
                    str(armed[f"agent_{side}_id"]),
                    [str(armed[f"ready_check_event_id_{side}"])],
                )
            await db_session.execute(
                text("UPDATE battle_tasks SET status = 'retired' WHERE category = 'general'")
            )
            await db_session.commit()
            token = await baseline._stamp_bind_lease(session_maker, battle_id)
            args = (armed["readiness_generation"], token, "clock regression")
    await db_session.execute(
        text(
            "UPDATE battles SET challenged_at = statement_timestamp() + INTERVAL '1 hour', "
            "challenge_expires_at = statement_timestamp() + INTERVAL '2 hours' "
            "WHERE id = CAST(:b AS UUID)"
        ),
        {"b": battle_id},
    )
    await db_session.commit()
    return writer, battle_id, args


async def test_abort_keeps_future_challenge_as_timestamp_lower_bound(abort_candidate, db_session):
    """Each abort writer remains legal when database time is behind the predecessor."""
    writer, battle_id, args = abort_candidate
    repo = BattleRepository(db_session)
    aborted = await getattr(repo, writer)(battle_id, *args)
    await db_session.commit()
    assert aborted is not None
    assert aborted["status"] == "aborted"
    assert aborted["ended_at"] >= aborted["challenged_at"]
    assert aborted["winner"] is None
    assert aborted["lease_token"] is None and aborted["lease_expires_at"] is None


async def test_running_start_preserves_future_queued_timestamp(db_session, task_id):
    """A persisted queue timestamp remains the start's lower bound after clock regression."""
    battle_id, _, _ = await baseline._build_queued_battle(db_session, task_id)
    result = await db_session.execute(
        text(
            "UPDATE battles SET queued_at = statement_timestamp() + INTERVAL '1 hour' "
            "WHERE id = CAST(:b AS UUID) RETURNING challenged_at, queued_at"
        ),
        {"b": battle_id},
    )
    predecessors = dict(result.mappings().one())
    await db_session.commit()

    started = await BattleRepository(db_session)._mark_running(battle_id, str(uuid4()), 600)
    await db_session.commit()
    assert started is not None
    assert started["status"] == "running"
    assert started["challenged_at"] == predecessors["challenged_at"]
    assert started["queued_at"] == predecessors["queued_at"]
    assert started["started_at"] >= predecessors["queued_at"]
    assert started["deadline_at"] - started["started_at"] == timedelta(seconds=600)


async def test_judge_completion_preserves_future_created_timestamp(db_session, task_id):
    """A fenced judge completion cannot precede its retained creation timestamp."""
    battle_id, _, _, _ = await baseline._battle_in_judging(db_session, task_id, votes=[])
    repo = BattleRepository(db_session)
    run_id = await repo.create_judge_run(
        battle_id,
        baseline.JUDGE_KIND_LLM,
        baseline.JUDGE_MODEL,
        baseline.replicate_seed(battle_id, 0),
        "ab",
    )
    await db_session.commit()
    assert run_id is not None
    token = str(uuid4())
    assert await repo.claim_judge_run(run_id, token, 180) is not None
    await db_session.commit()
    result = await db_session.execute(
        text(
            "UPDATE battle_judge_runs "
            "SET created_at = statement_timestamp() + INTERVAL '1 hour' "
            "WHERE id = CAST(:r AS UUID) RETURNING created_at"
        ),
        {"r": run_id},
    )
    created_at = result.scalar_one()
    await db_session.commit()

    assert await repo.complete_judge_run(run_id, str(uuid4()), "a") is None
    await db_session.commit()
    completed = await repo.complete_judge_run(run_id, token, "a")
    await db_session.commit()
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["created_at"] == created_at
    assert completed["completed_at"] >= created_at
    assert completed["lease_token"] is None and completed["lease_expires_at"] is None

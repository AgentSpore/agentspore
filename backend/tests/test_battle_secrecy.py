"""Rated-track TASK SECRECY tests (V67) against real Postgres.

The invariant under test, stated so it can be falsified:

    A challenge carries only a category/difficulty FILTER. The concrete task is
    chosen and snapshotted onto the battle ONLY at reserved -> queued, after
    both current-generation ready-ACKs are proven, inside the lease-fenced
    binding transaction. Until the battle is RUNNING, no API surface reveals the
    task id, title, prompt or rubric — not the detail route, not the list, not
    to a fighter's own key. The public task catalog exposes counts, never
    content. Two concurrent binds never choose the same task, and a filter with
    no fresh pool aborts honestly rather than crashing or leaking.

These run the REAL V65 + V66 + V67 migrations against testcontainers Postgres:
the secrecy gate is a status predicate in the route, the binding is a
lease-fenced data-modifying CTE, and the anti-reuse is a cooldown on a real
column — a mock proves none of it.

The mutation proof lives in ``test_queued_task_is_withheld_until_running``: if
the route stops nulling the task before 'running', that test's queued-state
assertions fail. (Demonstrated out-of-band by removing the `_sanitize_task`
nulling and re-running; see the delivery report.)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pydantic
import pytest
import pytest_asyncio
from conftest import split_sql_statements
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.api.deps import get_optional_user
from app.core.database import get_db
from app.main import app
from app.repositories.battle_repo import (
    MINIMUM_TASK_POOL,
    BattleRepository,
)
from app.schemas.battles import BattleStatus, CreateChallengeRequest, TaskSource
from app.services import battle_service as battle_service_module
from app.services.battle_service import (
    BattleService,
    ChallengeDenial,
    ChallengeDeniedError,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
V65_PATH = MIGRATIONS / "V65__agent_events.sql"
V66_PATH = MIGRATIONS / "V66__battles.sql"
V67_PATH = MIGRATIONS / "V67__battle_task_secrecy.sql"
V68_PATH = MIGRATIONS / "V68__battle_anti_abuse.sql"
V69_PATH = MIGRATIONS / "V69__battle_injection_stop_reason.sql"
V70_PATH = MIGRATIONS / "V70__battle_user_tasks.sql"

RUBRIC = [{"criterion": "correctness", "weight": 1.0}]
SECRET_PROMPT = "SECRET: implement a lock-free ring buffer in Rust."
SECRET_TITLE = "Ring buffer"

BASE_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT NOT NULL,
    is_verified BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    handle TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_hosted BOOLEAN NOT NULL DEFAULT FALSE,
    owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
"""

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _body_text(body: dict) -> str:
    return json.dumps(body).lower()


class _FakeRedis:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, seconds: int) -> None:
        return None


@pytest.fixture(autouse=True)
def redis_up(monkeypatch):
    fake = _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(battle_service_module, "get_redis", _get_redis)
    return fake


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(pg_container):
    async_url = pg_container.get_connection_url().replace("psycopg2", "asyncpg")
    eng = create_async_engine(async_url, future=True)
    sql = (
        f"{BASE_SCHEMA};{V65_PATH.read_text()};"
        f"{V66_PATH.read_text()};{V67_PATH.read_text()};{V68_PATH.read_text()};{V69_PATH.read_text()};{V70_PATH.read_text()}"
    )
    async with eng.begin() as conn:
        for stmt in split_sql_statements(sql):
            await conn.execute(text(stmt))
    yield eng
    await eng.dispose()


@pytest.fixture(scope="module")
def session_maker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="module")
async def db(session_maker):
    async with session_maker() as session:
        yield session


@pytest_asyncio.fixture(loop_scope="module")
async def owner_id(db) -> str:
    uid = str(uuid.uuid4())
    await db.execute(
        text("INSERT INTO users (id, email) VALUES (CAST(:id AS UUID), :e)"),
        {"id": uid, "e": f"owner-{uid[:8]}@example.test"},
    )
    await db.commit()
    return uid


@pytest_asyncio.fixture(loop_scope="module")
async def make_agent(db, owner_id):
    async def _make(*, owner: str | None = None) -> str:
        aid = str(uuid.uuid4())
        await db.execute(
            text(
                """
                INSERT INTO agents
                    (id, handle, name, is_active, is_hosted, owner_user_id,
                     available_for_battles)
                VALUES (CAST(:id AS UUID), :h, 'Fighter', TRUE, FALSE,
                        CAST(:owner AS UUID), TRUE)
                """
            ),
            {"id": aid, "h": f"a{aid[:8]}", "owner": owner or owner_id},
        )
        await db.commit()
        return aid

    return _make


async def _seed_pool(
    db, owner_id: str, *, category: str, difficulty: str, count: int
) -> list[str]:
    """Seed ``count`` ready tasks in one (category, difficulty) bucket."""
    repo = BattleRepository(db)
    ids: list[str] = []
    for i in range(count):
        ids.append(
            await repo.create_task(
                source=TaskSource.GENERATED,
                title=f"{SECRET_TITLE} {category}/{difficulty} {i}",
                prompt=f"{SECRET_PROMPT} ({category}/{difficulty} #{i})",
                rubric=RUBRIC,
                time_limit_seconds=600,
                category=category,
                difficulty=difficulty,
                created_by_user_id=None,
            )
        )
    await db.commit()
    return ids


async def _reserved_and_acked(
    db, owner_id: str, make_agent, *, category: str | None, difficulty: str | None
) -> dict:
    """Drive a battle to 'reserved' with both ready-ACKs recorded."""
    svc = BattleService(db)
    agent_a = await make_agent()
    agent_b = await make_agent()
    battle_id = await svc.create_challenge(
        task_category=category,
        task_difficulty=difficulty,
        agent_a_id=agent_a,
        challenger_owner_user_id=owner_id,
        agent_b_id=agent_b,
    )
    await db.commit()
    assert await svc.accept(battle_id, owner_id) is not None
    await db.commit()
    armed = await svc.arm_readiness(battle_id)
    await db.commit()
    assert armed is not None
    for side, akey in (("a", "agent_a_id"), ("b", "agent_b_id")):
        await svc.events.mark_acked(
            str(armed[akey]), [str(armed[f"ready_check_event_id_{side}"])]
        )
    await db.commit()
    return armed


async def _lease(db, battle_id: str, seconds: int = 15) -> str:
    """Stamp a live processing lease on a battle; return the token."""
    token = str(uuid.uuid4())
    await db.execute(
        text(
            """
            UPDATE battles
            SET lease_token = CAST(:t AS UUID),
                lease_expires_at = NOW() + make_interval(secs => :s)
            WHERE id = CAST(:b AS UUID)
            """
        ),
        {"t": token, "s": seconds, "b": battle_id},
    )
    await db.commit()
    return token


# ── (f) challenge-create takes a filter, not a task id ──────────────────────


def test_challenge_request_has_no_task_id_and_ignores_it():
    """CreateChallengeRequest carries category/difficulty; task_id is gone."""
    assert "task_id" not in CreateChallengeRequest.model_fields
    assert "task_category" in CreateChallengeRequest.model_fields
    assert "task_difficulty" in CreateChallengeRequest.model_fields

    # A body still carrying the removed task_id is simply ignored (Pydantic
    # drops the extra), not honoured — no task can enter via the wire.
    req = CreateChallengeRequest(
        task_id=str(uuid.uuid4()),
        task_category="Backend",
        task_difficulty="hard",
        agent_a_id=uuid.uuid4(),
    )
    assert not hasattr(req, "task_id")
    assert req.task_category == "Backend"
    assert req.task_difficulty.value == "hard"


def test_blank_or_bad_difficulty_rejected():
    with pytest.raises(pydantic.ValidationError):
        CreateChallengeRequest(
            task_category="", task_difficulty=None, agent_a_id=uuid.uuid4()
        )
    with pytest.raises(pydantic.ValidationError):
        CreateChallengeRequest(
            task_category="backend", task_difficulty="trivial", agent_a_id=uuid.uuid4()
        )


# ── (b) binding happens at reserved -> queued, snapshots, marks used ─────────


async def test_bind_at_reserved_to_queued_snapshots_and_marks_used(
    db, owner_id, make_agent
):
    """The task is chosen ONLY here: reserved carries none, queued carries all."""
    await _seed_pool(db, owner_id, category="bindcat", difficulty="hard",
                     count=MINIMUM_TASK_POOL + 5)
    armed = await _reserved_and_acked(
        db, owner_id, make_agent, category="bindcat", difficulty="hard"
    )
    battle_id = str(armed["id"])

    # Reserved: no task bound, no snapshot.
    reserved = await BattleRepository(db).get(battle_id)
    assert reserved["status"] == BattleStatus.RESERVED.value
    assert reserved["task_id"] is None
    assert reserved["task_prompt_snapshot"] is None
    assert reserved["task_title_snapshot"] is None

    token = await _lease(db, battle_id)
    queued = await BattleService(db).try_queue(
        battle_id, armed["readiness_generation"], token
    )
    await db.commit()
    assert queued is not None
    assert queued["status"] == BattleStatus.QUEUED.value

    # Queued: fully bound, snapshot frozen, matching the requested filter.
    bound = await BattleRepository(db).get(battle_id)
    assert bound["task_id"] is not None
    assert bound["task_prompt_snapshot"].startswith("SECRET")
    assert bound["task_title_snapshot"].startswith(SECRET_TITLE)
    assert bound["task_rubric_snapshot"] == RUBRIC
    assert bound["time_limit_seconds_snapshot"] == 600

    # The chosen task is marked used exactly once.
    row = (
        await db.execute(
            text(
                "SELECT category, difficulty, use_count, last_used_at "
                "FROM battle_tasks WHERE id = CAST(:t AS UUID)"
            ),
            {"t": str(bound["task_id"])},
        )
    ).mappings().one()
    assert row["category"] == "bindcat"
    assert row["difficulty"] == "hard"
    assert row["use_count"] == 1
    assert row["last_used_at"] is not None


async def test_one_ack_never_binds(db, owner_id, make_agent):
    """A single ACK leaves the battle reserved and the task unbound."""
    await _seed_pool(db, owner_id, category="oneack", difficulty="easy",
                     count=MINIMUM_TASK_POOL + 2)
    svc = BattleService(db)
    agent_a = await make_agent()
    agent_b = await make_agent()
    battle_id = await svc.create_challenge(
        task_category="oneack", task_difficulty="easy",
        agent_a_id=agent_a, challenger_owner_user_id=owner_id, agent_b_id=agent_b,
    )
    await db.commit()
    await svc.accept(battle_id, owner_id)
    await db.commit()
    armed = await svc.arm_readiness(battle_id)
    await db.commit()
    # Only A acks.
    await svc.events.mark_acked(
        str(armed["agent_a_id"]), [str(armed["ready_check_event_id_a"])]
    )
    await db.commit()

    token = await _lease(db, battle_id)
    assert await svc.try_queue(battle_id, armed["readiness_generation"], token) is None
    await db.commit()
    still = await BattleRepository(db).get(battle_id)
    assert still["status"] == BattleStatus.RESERVED.value
    assert still["task_id"] is None


async def test_wrong_lease_token_cannot_bind(db, owner_id, make_agent):
    """Both ACKed, but a stale token binds nothing and cools no task."""
    await _seed_pool(db, owner_id, category="leasecat", difficulty="medium",
                     count=MINIMUM_TASK_POOL + 2)
    armed = await _reserved_and_acked(
        db, owner_id, make_agent, category="leasecat", difficulty="medium"
    )
    battle_id = str(armed["id"])
    await _lease(db, battle_id)  # real token stamped on the row
    wrong = str(uuid.uuid4())

    assert await BattleService(db).try_queue(
        battle_id, armed["readiness_generation"], wrong
    ) is None
    await db.commit()
    still = await BattleRepository(db).get(battle_id)
    assert still["status"] == BattleStatus.RESERVED.value
    assert still["task_id"] is None
    # No task was cooled down by the failed bind.
    used = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM battle_tasks "
                "WHERE category = 'leasecat' AND use_count > 0"
            )
        )
    ).scalar_one()
    assert used == 0


async def test_expired_lease_cannot_bind(db, owner_id, make_agent):
    """A lapsed processing lease binds nothing even with both ACKs."""
    await _seed_pool(db, owner_id, category="explease", difficulty="hard",
                     count=MINIMUM_TASK_POOL + 2)
    armed = await _reserved_and_acked(
        db, owner_id, make_agent, category="explease", difficulty="hard"
    )
    battle_id = str(armed["id"])
    token = await _lease(db, battle_id, seconds=-1)  # already expired

    assert await BattleService(db).try_queue(
        battle_id, armed["readiness_generation"], token
    ) is None
    await db.commit()
    assert (await BattleRepository(db).get(battle_id))["task_id"] is None


# ── (c) concurrent binds never choose the same task ─────────────────────────


async def test_concurrent_binds_choose_different_tasks(
    db, owner_id, make_agent, session_maker
):
    """Two battles binding at once select two distinct tasks, each used once.

    The global advisory lock serialises the two binds, and the reuse cooldown
    excludes a just-bound task from the second pool, so the same task can never
    be chosen twice.
    """
    await _seed_pool(db, owner_id, category="concur", difficulty="hard",
                     count=MINIMUM_TASK_POOL + 5)
    a1 = await _reserved_and_acked(
        db, owner_id, make_agent, category="concur", difficulty="hard"
    )
    a2 = await _reserved_and_acked(
        db, owner_id, make_agent, category="concur", difficulty="hard"
    )
    t1 = await _lease(db, str(a1["id"]))
    t2 = await _lease(db, str(a2["id"]))

    async def _bind(battle: dict, token: str) -> str | None:
        async with session_maker() as s:
            bound = await BattleService(s).try_queue(
                str(battle["id"]), battle["readiness_generation"], token
            )
            await s.commit()
            return str(bound["task_id"]) if bound else None

    picked = await asyncio.gather(_bind(a1, t1), _bind(a2, t2))
    assert all(picked), f"both binds should succeed, got {picked}"
    assert picked[0] != picked[1], "two concurrent binds chose the same task"

    for task_id in picked:
        uc = (
            await db.execute(
                text("SELECT use_count FROM battle_tasks WHERE id = CAST(:t AS UUID)"),
                {"t": task_id},
            )
        ).scalar_one()
        assert uc == 1


# ── (d) no eligible task -> honest abort, not a crash ───────────────────────


async def test_no_fresh_task_aborts_reserved_battle_honestly(
    db, owner_id, make_agent
):
    """Both ACKed, filter has < minimum fresh tasks -> reserved -> aborted.

    Named behaviour: the battle ends 'aborted' with an honest verdict_reason,
    both reservations are released, no battle_turn exists, and Elo is untouched.

    The pool must be adequate at CHALLENGE time (else the challenge is refused
    up front) but exhausted by BIND time — the "tasks retired between challenge
    and readiness" case. So it is seeded, the battle reaches reserved+acked, and
    then every task in the bucket is retired before binding is attempted.
    """
    await _seed_pool(db, owner_id, category="emptybucket", difficulty="hard",
                     count=MINIMUM_TASK_POOL + 1)
    armed = await _reserved_and_acked(
        db, owner_id, make_agent, category="emptybucket", difficulty="hard"
    )
    battle_id = str(armed["id"])
    # Retire the whole bucket after the challenge was admitted.
    await db.execute(
        text("UPDATE battle_tasks SET status = 'retired' WHERE category = 'emptybucket'")
    )
    await db.commit()
    token = await _lease(db, battle_id)
    svc = BattleService(db)

    # try_queue cannot bind (empty pool).
    assert await svc.try_queue(battle_id, armed["readiness_generation"], token) is None
    # The pool-exhausted abort fires, re-proving readiness in its own CAS.
    aborted = await svc.abort_pool_exhausted(
        battle_id, armed["readiness_generation"], token
    )
    await db.commit()
    assert aborted is not None
    assert aborted["status"] == BattleStatus.ABORTED.value
    assert "exhausted" in aborted["verdict_reason"]
    assert aborted["task_id"] is None
    assert aborted["winner"] is None
    assert aborted["elo_a_after"] is None and aborted["elo_b_after"] is None

    # Both reservations released; no battle_turn was ever emitted.
    held = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM battle_reservations "
                "WHERE battle_id = CAST(:b AS UUID)"
            ),
            {"b": battle_id},
        )
    ).scalar_one()
    assert held == 0
    turns = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM agent_events "
                "WHERE type = 'battle_turn' AND payload::text LIKE :like"
            ),
            {"like": f"%{battle_id}%"},
        )
    ).scalar_one()
    assert turns == 0


async def test_pool_exhausted_abort_does_not_fire_when_not_ready(
    db, owner_id, make_agent
):
    """abort_pool_exhausted must NOT abort a battle still waiting for an ACK."""
    await _seed_pool(db, owner_id, category="emptybucket2", difficulty="hard",
                     count=MINIMUM_TASK_POOL + 1)
    svc = BattleService(db)
    agent_a = await make_agent()
    agent_b = await make_agent()
    battle_id = await svc.create_challenge(
        task_category="emptybucket2", task_difficulty="hard",
        agent_a_id=agent_a, challenger_owner_user_id=owner_id, agent_b_id=agent_b,
    )
    await db.commit()
    await svc.accept(battle_id, owner_id)
    await db.commit()
    armed = await svc.arm_readiness(battle_id)
    await db.commit()
    # Retire the bucket so the pool is empty, but nobody acks: readiness is NOT
    # proven, so the exhaustion abort must still refuse to fire.
    await db.execute(
        text("UPDATE battle_tasks SET status = 'retired' WHERE category = 'emptybucket2'")
    )
    await db.commit()
    token = await _lease(db, battle_id)
    assert await svc.abort_pool_exhausted(
        battle_id, armed["readiness_generation"], token
    ) is None
    await db.commit()
    assert (
        await BattleRepository(db).get(battle_id)
    )["status"] == BattleStatus.RESERVED.value


# ── (e) public task catalog carries no content ──────────────────────────────


async def test_list_task_pools_leaks_no_content(db, owner_id):
    await _seed_pool(db, owner_id, category="poolcat", difficulty="easy", count=3)
    pools = await BattleRepository(db).list_task_pools()
    assert pools, "expected at least one pool bucket"
    for p in pools:
        assert set(p.keys()) == {
            "category", "difficulty", "fresh_count", "challenge_available"
        }
        assert "prompt" not in p and "rubric" not in p and "title" not in p
        assert "id" not in p


@pytest_asyncio.fixture(loop_scope="module")
async def client(db):
    async def override_get_db():
        yield db

    async def _anon():
        return None

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_optional_user] = _anon
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_tasks_route_returns_pool_aggregates_only(client, db, owner_id):
    await _seed_pool(db, owner_id, category="routecat", difficulty="medium", count=2)
    resp = await client.get("/api/v1/battles/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert "pools" in body and "minimum_pool_size" in body and "cooldown_days" in body
    assert "secret" not in resp.text.lower(), "task prompt leaked into the catalog"
    for p in body["pools"]:
        assert "prompt" not in p and "rubric" not in p and "title" not in p
        assert "id" not in p


# ── (a) task withheld until running, revealed at running ─────────────────────


async def _bind_to_queued(db, owner_id, make_agent, category, difficulty) -> dict:
    await _seed_pool(db, owner_id, category=category, difficulty=difficulty,
                     count=MINIMUM_TASK_POOL + 2)
    armed = await _reserved_and_acked(
        db, owner_id, make_agent, category=category, difficulty=difficulty
    )
    token = await _lease(db, str(armed["id"]))
    queued = await BattleService(db).try_queue(
        str(armed["id"]), armed["readiness_generation"], token
    )
    await db.commit()
    assert queued is not None
    return queued


async def test_pre_running_states_withhold_task_via_get_battle(
    client, db, owner_id, make_agent
):
    """challenge_pending / accepted / reserved detail hides id + snapshots."""
    svc = BattleService(db)
    await _seed_pool(db, owner_id, category="prerun", difficulty="hard",
                     count=MINIMUM_TASK_POOL + 2)
    agent_a = await make_agent()
    agent_b = await make_agent()
    battle_id = await svc.create_challenge(
        task_category="prerun", task_difficulty="hard",
        agent_a_id=agent_a, challenger_owner_user_id=owner_id, agent_b_id=agent_b,
    )
    await db.commit()

    # challenge_pending
    resp = await client.get(f"/api/v1/battles/{battle_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] is None
    assert body["task_prompt_snapshot"] is None
    assert body["task_title_snapshot"] is None
    assert body["task_content_withheld"] is True
    # The requested filter IS visible — it reveals no concrete task.
    assert body["task_category_filter"] == "prerun"
    assert body["task_difficulty_filter"] == "hard"
    assert "secret" not in resp.text.lower()

    # accepted
    await svc.accept(battle_id, owner_id)
    await db.commit()
    body = (await client.get(f"/api/v1/battles/{battle_id}")).json()
    assert body["task_id"] is None and body["task_content_withheld"] is True

    # reserved
    await svc.arm_readiness(battle_id)
    await db.commit()
    body = (await client.get(f"/api/v1/battles/{battle_id}")).json()
    assert body["task_id"] is None and body["task_content_withheld"] is True


async def test_queued_task_is_withheld_until_running(client, db, owner_id, make_agent):
    """MUTATION ANCHOR: a queued battle is bound internally yet withheld.

    If get_battle stops nulling the task before 'running' (remove the
    _sanitize_task withhold), the queued assertions below fail — that is the
    mutation-honest proof of the secrecy gate.
    """
    queued = await _bind_to_queued(db, owner_id, make_agent, "qwithheld", "hard")
    battle_id = str(queued["id"])

    # Internally bound.
    assert (await BattleRepository(db).get(battle_id))["task_id"] is not None

    # Publicly withheld.
    body = (await client.get(f"/api/v1/battles/{battle_id}")).json()
    assert body["status"] == BattleStatus.QUEUED.value
    assert body["task_id"] is None
    assert body["task_prompt_snapshot"] is None
    assert body["task_title_snapshot"] is None
    assert body["task_content_withheld"] is True
    assert "secret" not in _body_text(body)

    # And on the public list.
    listing = (await client.get("/api/v1/battles?status=queued")).json()
    mine = [b for b in listing if b["id"] == battle_id]
    assert mine and mine[0]["task_id"] is None
    assert mine[0]["task_content_withheld"] is True


async def test_running_battle_reveals_the_snapshot(client, db, owner_id, make_agent):
    """Once running, the snapshot is public — no longer a pre-fetch advantage."""
    queued = await _bind_to_queued(db, owner_id, make_agent, "runreveal", "hard")
    battle_id = str(queued["id"])
    # Move to running via the state-machine primitive.
    running = await BattleRepository(db)._mark_running(battle_id, str(uuid.uuid4()), 600)
    await db.commit()
    assert running is not None and running["status"] == BattleStatus.RUNNING.value

    body = (await client.get(f"/api/v1/battles/{battle_id}")).json()
    assert body["task_content_withheld"] is False
    assert body["task_id"] is not None
    assert body["task_prompt_snapshot"].startswith("SECRET")
    assert body["task_title_snapshot"].startswith(SECRET_TITLE)


# ── (g) MUTATION ANCHOR: public (secret=FALSE) tasks are quarantined ─────────
#
# Every earlier test seeds FRESH tasks, which default secret=TRUE, so they pass
# whether or not the pool queries filter `secret = TRUE` — they do not exercise
# the quarantine invariant at all. These two do: they make a bucket PUBLIC
# (the shape V67 gives every pre-V67 catalog row) and assert it can neither be
# challenged nor bound. Drop any `AND secret = TRUE` from the pool queries and
# one of these goes red.


async def _make_pool_public(db, ids: list[str]) -> None:
    """Flip a seeded bucket to secret=FALSE — the quarantined legacy shape."""
    await db.execute(
        text(
            "UPDATE battle_tasks SET secret = FALSE "
            "WHERE id = ANY(CAST(:ids AS UUID[]))"
        ),
        {"ids": ids},
    )
    await db.commit()


async def test_public_pool_neither_advertises_nor_admits(db, owner_id, make_agent):
    """A public-only bucket shows empty in the catalog and refuses a challenge.

    Exercises list_task_pools, diagnose_challenge and create_challenge: all
    three must count only SECRET tasks. Seeds MORE than the minimum, then makes
    every row public, so a COUNT that forgot the secret filter would still clear
    the gate.
    """
    cat, diff = "publiconly", "hard"
    ids = await _seed_pool(db, owner_id, category=cat, difficulty=diff,
                           count=MINIMUM_TASK_POOL + 3)
    await _make_pool_public(db, ids)

    # Catalog: the bucket advertises no bindable pool (absent or fresh_count 0).
    pools = await BattleRepository(db).list_task_pools()
    mine = [p for p in pools if p["category"] == cat and p["difficulty"] == diff]
    assert not mine or (
        mine[0]["fresh_count"] == 0 and mine[0]["challenge_available"] is False
    ), f"public tasks leaked into the catalog: {mine}"

    # Admission: the fresh SECRET pool is empty, so the challenge is refused.
    svc = BattleService(db)
    agent_a = await make_agent()
    agent_b = await make_agent()
    with pytest.raises(ChallengeDeniedError) as ei:
        await svc.create_challenge(
            task_category=cat, task_difficulty=diff, agent_a_id=agent_a,
            challenger_owner_user_id=owner_id, agent_b_id=agent_b,
        )
    assert ei.value.reason == ChallengeDenial.INSUFFICIENT_TASK_POOL
    await db.rollback()


async def test_task_made_public_after_challenge_never_binds(db, owner_id, make_agent):
    """MUTATION ANCHOR for admit_to_queue's `AND t.secret = TRUE`.

    Adequate SECRET pool at challenge time, then every matching task is made
    public before binding — the "catalog leaked between challenge and readiness"
    case, mirroring the retired-task abort. The bind must see an empty pool:
    try_queue binds nothing and the pool-exhausted abort fires honestly. Remove
    the secret predicate from the binding CTE and try_queue would bind a public
    task, so this test flips green->red.
    """
    cat, diff = "flipsecret", "hard"
    await _seed_pool(db, owner_id, category=cat, difficulty=diff,
                     count=MINIMUM_TASK_POOL + 2)
    armed = await _reserved_and_acked(
        db, owner_id, make_agent, category=cat, difficulty=diff
    )
    battle_id = str(armed["id"])
    # The whole bucket becomes public after the challenge was admitted.
    await db.execute(
        text("UPDATE battle_tasks SET secret = FALSE WHERE category = :c"),
        {"c": cat},
    )
    await db.commit()
    token = await _lease(db, battle_id)
    svc = BattleService(db)

    assert await svc.try_queue(battle_id, armed["readiness_generation"], token) is None
    aborted = await svc.abort_pool_exhausted(
        battle_id, armed["readiness_generation"], token
    )
    await db.commit()
    assert aborted is not None
    assert aborted["status"] == BattleStatus.ABORTED.value
    assert aborted["task_id"] is None


# ── (h) MUTATION ANCHOR: a bound task is retired, never reused ───────────────
#
# A task revealed to fighters (the API publishes the snapshot once 'running') is
# public forever, so strict secrecy is incompatible with reuse-after-cooldown:
# binding must RETIRE the content permanently, across every duplicate-content
# row, not merely cool one row down. These prove that.


async def test_bound_task_is_retired_from_the_pool(db, owner_id, make_agent):
    """F1: the bound task is secret=FALSE afterward — retired, not just cooled.

    MUTATION ANCHOR: drop `secret = FALSE` from the bind's retire CTE and the
    bound row stays secret=TRUE, so this assertion flips green->red. A retired
    row is then excluded by every bindable-pool query (proven by the section (g)
    public-task tests), so it can never bind to a second rated battle — even
    once its reuse cooldown lapses.
    """
    cat, diff = "retirecat", "hard"
    await _seed_pool(db, owner_id, category=cat, difficulty=diff,
                     count=MINIMUM_TASK_POOL + 1)
    armed = await _reserved_and_acked(
        db, owner_id, make_agent, category=cat, difficulty=diff
    )
    battle_id = str(armed["id"])
    token = await _lease(db, battle_id)
    queued = await BattleService(db).try_queue(
        battle_id, armed["readiness_generation"], token
    )
    await db.commit()
    assert queued is not None
    bound_task = str(queued["task_id"])

    secret = (
        await db.execute(
            text("SELECT secret FROM battle_tasks WHERE id = CAST(:t AS UUID)"),
            {"t": bound_task},
        )
    ).scalar_one()
    assert secret is False, "a revealed (bound) task must be retired from the pool"


async def test_binding_retires_every_duplicate_content_sibling(
    db, owner_id, make_agent
):
    """F3: duplicate-content rows are burned together, not just the chosen id.

    Seed each of the minimum-plus distinct prompts TWICE, so whichever prompt the
    bind picks has a sibling row carrying the same (now-public) content. After a
    single bind, NO row with the bound prompt may remain secret.

    MUTATION ANCHOR: retire on `id = c.id` instead of `prompt = c.prompt` and the
    sibling stays secret=TRUE -> the count below is 1, not 0 -> red. (Every prompt
    is duplicated, so the mutation fails regardless of which prompt was drawn.)
    """
    cat, diff = "dupcat", "hard"
    repo = BattleRepository(db)
    for i in range(MINIMUM_TASK_POOL + 1):
        prompt = f"SECRET dup content #{i}"
        for _ in range(2):  # two ready rows share each prompt
            await repo.create_task(
                source=TaskSource.GENERATED,
                title=f"{SECRET_TITLE} dup {i}",
                prompt=prompt,
                rubric=RUBRIC,
                time_limit_seconds=600,
                category=cat,
                difficulty=diff,
                created_by_user_id=None,
            )
    await db.commit()

    armed = await _reserved_and_acked(
        db, owner_id, make_agent, category=cat, difficulty=diff
    )
    battle_id = str(armed["id"])
    token = await _lease(db, battle_id)
    queued = await BattleService(db).try_queue(
        battle_id, armed["readiness_generation"], token
    )
    await db.commit()
    assert queued is not None
    bound_prompt = queued["task_prompt_snapshot"]

    still_secret = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM battle_tasks "
                "WHERE prompt = :p AND secret = TRUE"
            ),
            {"p": bound_prompt},
        )
    ).scalar_one()
    assert still_secret == 0, "a duplicate-content sibling survived retirement"


async def test_binding_retires_semantic_content_variants(db, owner_id, make_agent):
    """F3 (semantic): whitespace/case variants of the bound content are burned.

    Each content is seeded as TWO rows that differ only by case and whitespace
    (e.g. `"SECRET solve problem 3"` and `"   SECRET   SOLVE   PROBLEM   3   "`).
    They normalize to the SAME canonical content key, so after one variant binds
    and is revealed, its twin must also be secret=FALSE — otherwise the twin is
    the exact same (now-public) task, still bindable.

    MUTATION ANCHOR: revert the retire predicate to raw `t.prompt = c.prompt` and
    the case/whitespace twin survives (different raw text) -> the count below is
    >= 1, not 0 -> red. Every content is variant-paired, so the mutation fails
    regardless of which content was drawn.
    """
    cat, diff = "semdupcat", "hard"
    repo = BattleRepository(db)
    for i in range(MINIMUM_TASK_POOL + 1):
        base = f"SECRET solve problem {i}"
        # Same content, differing only by case + surrounding/internal whitespace.
        variant = f"   {base.upper().replace(' ', '   ')}   "
        for prompt in (base, variant):
            await repo.create_task(
                source=TaskSource.GENERATED,
                title=f"{SECRET_TITLE} sem {i}",
                prompt=prompt,
                rubric=RUBRIC,
                time_limit_seconds=600,
                category=cat,
                difficulty=diff,
                created_by_user_id=None,
            )
    await db.commit()

    armed = await _reserved_and_acked(
        db, owner_id, make_agent, category=cat, difficulty=diff
    )
    battle_id = str(armed["id"])
    token = await _lease(db, battle_id)
    queued = await BattleService(db).try_queue(
        battle_id, armed["readiness_generation"], token
    )
    await db.commit()
    assert queued is not None
    bound_prompt = queued["task_prompt_snapshot"]

    # Count rows whose NORMALIZED content matches the bound task and are still
    # secret — the case/whitespace twin must have been retired along with it.
    still_secret = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM battle_tasks "
                "WHERE regexp_replace(btrim(lower(prompt)), '\\s+', ' ', 'g') "
                "    = regexp_replace(btrim(lower(:p)), '\\s+', ' ', 'g') "
                "  AND secret = TRUE"
            ),
            {"p": bound_prompt},
        )
    ).scalar_one()
    assert still_secret == 0, "a case/whitespace content variant survived retirement"

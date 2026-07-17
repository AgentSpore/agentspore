"""Tests for POST /battles/{id}/turns — the fighter's only way in (step 9).

The invariant under test, stated so it can be falsified:

    Only a fighter of THIS battle, only while it is running, only before the
    server's deadline, and only forward: no client clock, no re-used slot, no
    second last word, no unbounded body.

Every one of these was argued in a docstring and asserted nowhere, which this
session has three times shown to be worth nothing. So they run over HTTP against
the REAL app and the REAL V65+V66 migrations on testcontainers Postgres: the
rules live in a Depends, a status check and two database constraints, and a mock
would only prove a mock returns what it was told.

Each test asserts the REJECTION *and* that no row was written — a 4xx that still
persists the submission would be the worst outcome and the easiest to miss.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from conftest import split_sql_statements
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.core.database import get_db
from app.main import app
from app.repositories.agent_event_repo import AgentEventRepository
from app.repositories.battle_repo import BattleRepository
from app.schemas.battles import MAX_SUBMISSION_CHARS, BattleStatus, TaskSource
from app.services.agent_service import AgentService
from app.services.battle_runner import (
    BATTLE_LEASE_SECONDS,
    RECONCILE_BATCH,
    RUNNING_MAX_ATTEMPTS,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
V65_PATH = MIGRATIONS / "V65__agent_events.sql"
V66_PATH = MIGRATIONS / "V66__battles.sql"
V67_PATH = MIGRATIONS / "V67__battle_task_secrecy.sql"

RUBRIC = [{"key": "correctness", "description": "Does it work?", "weight": 1.0}]

# api_key_hash is the column get_agent_by_api_key_hash actually authenticates
# against, so it must be real here — the auth path is under test, not stubbed.
BASE_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    handle TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_hosted BOOLEAN NOT NULL DEFAULT FALSE,
    api_key_hash TEXT,
    owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
"""

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(pg_container):
    async_url = pg_container.get_connection_url().replace("psycopg2", "asyncpg")
    eng = create_async_engine(async_url, future=True)
    sql = f"{BASE_SCHEMA};{V65_PATH.read_text()};{V66_PATH.read_text()};{V67_PATH.read_text()}"
    async with eng.begin() as conn:
        for stmt in split_sql_statements(sql):
            if stmt.strip():
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
async def client(session_maker):
    """The real app, with only the DB swapped for the container.

    get_db is the ONLY override: get_agent_by_api_key depends on it and is
    otherwise untouched, so the X-API-Key hashing and the is_active predicate
    are exercised for real. Overriding the auth dep would delete the 403 test's
    entire subject.
    """

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _new_owner(session) -> str:
    uid = str(uuid.uuid4())
    await session.execute(
        text("INSERT INTO users (id, email) VALUES (CAST(:id AS UUID), :e)"),
        {"id": uid, "e": f"o-{uid[:8]}@example.test"},
    )
    return uid


async def _new_agent(session, owner_id: str) -> tuple[str, str]:
    """Insert an agent with a REAL hashed key. Returns (agent_id, plaintext key)."""
    aid = str(uuid.uuid4())
    api_key = f"sk-test-{uuid.uuid4().hex}"
    await session.execute(
        text(
            "INSERT INTO agents (id, handle, name, api_key_hash, owner_user_id) "
            "VALUES (CAST(:id AS UUID), :h, 'F', :kh, CAST(:o AS UUID))"
        ),
        {"id": aid, "h": f"f-{aid[:8]}", "kh": AgentService.hash_api_key(api_key), "o": owner_id},
    )
    return aid, api_key


@pytest_asyncio.fixture(loop_scope="module")
async def task_id(db) -> str:
    owner = await _new_owner(db)
    tid = await BattleRepository(db).create_task(
        source=TaskSource.GENERATED,
        category="general",
        title="Write a parser",
        prompt="Parse this log format.",
        rubric=RUBRIC,
        time_limit_seconds=600,
        created_by_user_id=owner,
    )
    await db.commit()
    return tid


async def _running_battle(db, task_id: str) -> dict:
    """Drive a battle to 'running' through the real state machine.

    Not an INSERT of a 'running' row: the endpoint reads agent_a_id/agent_b_id,
    status and deadline_at, and a hand-made row could carry a combination the
    machine cannot produce — the test would then assert against a fiction.
    """
    repo = BattleRepository(db)
    events = AgentEventRepository(db)

    owner_a, owner_b = await _new_owner(db), await _new_owner(db)
    agent_a, key_a = await _new_agent(db, owner_a)
    agent_b, key_b = await _new_agent(db, owner_b)

    battle_id = await repo._create_battle(
        task_id=task_id,
        agent_a_id=agent_a,
        agent_a_owner_snapshot=owner_a,
        challenge_ttl_seconds=3600,
        agent_b_id=agent_b,
        agent_b_owner_snapshot=owner_b,
    )
    assert await repo._mark_accepted(battle_id) is not None
    assert len(await repo.reserve_both(battle_id, agent_a, agent_b, 600)) == 2
    ev_a = await events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
    ev_b = await events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
    row = await repo.arm_readiness(battle_id, ev_a, ev_b, 60)
    assert await repo._mark_queued(battle_id, row["readiness_generation"]) is not None
    assert await repo._mark_running(battle_id, str(uuid.uuid4()), 600) is not None
    await db.commit()

    return {
        "id": battle_id,
        "agent_a": agent_a,
        "key_a": key_a,
        "agent_b": agent_b,
        "key_b": key_b,
    }


async def _submissions(session_maker, battle_id: str) -> list[dict]:
    async with session_maker() as session:
        return await BattleRepository(session).list_submissions(battle_id)


async def _expire_deadline(session_maker, battle_id: str) -> None:
    """Age the whole timestamp chain, because V66 enforces its coherence."""
    async with session_maker() as session:
        await session.execute(
            text(
                "UPDATE battles SET challenged_at = NOW() - INTERVAL '30 minutes', "
                "queued_at = NOW() - INTERVAL '20 minutes', "
                "started_at = NOW() - INTERVAL '10 minutes', "
                "deadline_at = NOW() - INTERVAL '1 second' WHERE id = CAST(:b AS UUID)"
            ),
            {"b": battle_id},
        )
        await session.commit()


class TestHappyPath:
    """The endpoint must actually work, or every rejection below is vacuous."""

    async def test_a_fighter_can_submit_a_checkpoint_and_then_a_final(
        self, client, db, session_maker, task_id
    ) -> None:
        battle = await _running_battle(db, task_id)

        first = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "partial thoughts", "seq_no": 1},
            headers={"X-API-Key": battle["key_a"]},
        )
        assert first.status_code == 200, first.text
        assert first.json() == {"status": "accepted", "side": "a", "seq_no": 1, "is_final": False}

        final = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "my answer", "seq_no": 2, "is_final": True, "tokens_used": 12},
            headers={"X-API-Key": battle["key_a"]},
        )
        assert final.status_code == 200, final.text

        rows = await _submissions(session_maker, battle["id"])
        assert [(r["seq_no"], r["is_final"]) for r in rows] == [(1, False), (2, True)]
        assert all(str(r["side"]) == "a" for r in rows)

    async def test_the_side_is_derived_from_the_key_not_the_body(
        self, client, db, session_maker, task_id
    ) -> None:
        # B's key must produce side 'b' even though the body cannot say so —
        # there is no side field, and adding one would be the bug.
        battle = await _running_battle(db, task_id)
        response = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "b speaks", "seq_no": 1, "side": "a"},
            headers={"X-API-Key": battle["key_b"]},
        )
        assert response.status_code == 200
        assert response.json()["side"] == "b"

        rows = await _submissions(session_maker, battle["id"])
        assert [str(r["side"]) for r in rows] == ["b"]


class TestMembership:
    """403 — the rule with the most to lose from a green-but-blind test."""

    async def test_a_non_participant_cannot_submit_to_someone_elses_battle(
        self, client, db, session_maker, task_id
    ) -> None:
        battle = await _running_battle(db, task_id)
        # A real, VALID, active agent — authentication succeeds and the request
        # dies on membership. An invalid key would prove 401, not 403.
        outsider_owner = await _new_owner(db)
        _outsider_id, outsider_key = await _new_agent(db, outsider_owner)
        await db.commit()

        response = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "let me in", "seq_no": 1},
            headers={"X-API-Key": outsider_key},
        )

        assert response.status_code == 403, response.text
        assert "not a fighter" in response.json()["detail"]
        # The rejection must also not have written anything.
        assert await _submissions(session_maker, battle["id"]) == []

    async def test_an_unknown_key_is_401_not_403(self, client, db, task_id) -> None:
        battle = await _running_battle(db, task_id)
        response = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "x", "seq_no": 1},
            headers={"X-API-Key": "sk-test-nonexistent"},
        )
        assert response.status_code == 401


class TestDeadline:
    """409 — the server's clock is the only clock."""

    async def test_a_submission_after_the_deadline_is_rejected(
        self, client, db, session_maker, task_id
    ) -> None:
        battle = await _running_battle(db, task_id)
        await _expire_deadline(session_maker, battle["id"])

        response = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "too late", "seq_no": 1},
            headers={"X-API-Key": battle["key_a"]},
        )

        assert response.status_code == 409, response.text
        assert "deadline" in response.json()["detail"]
        assert await _submissions(session_maker, battle["id"]) == []

    async def test_the_client_cannot_backdate_itself_past_the_deadline(
        self, client, db, session_maker, task_id
    ) -> None:
        # The attack the schema is shaped to make impossible: claim you finished
        # an hour ago. The fields are not merely ignored — they cannot be said.
        battle = await _running_battle(db, task_id)
        await _expire_deadline(session_maker, battle["id"])

        response = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={
                "content": "I finished on time, honest",
                "seq_no": 1,
                "finished_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "received_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            },
            headers={"X-API-Key": battle["key_a"]},
        )

        assert response.status_code == 409
        assert await _submissions(session_maker, battle["id"]) == []

    async def test_the_server_clock_times_the_submission_not_the_client(
        self, client, db, session_maker, task_id
    ) -> None:
        battle = await _running_battle(db, task_id)
        before = datetime.now(UTC)

        response = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={
                "content": "on time",
                "seq_no": 1,
                "received_at": "2020-01-01T00:00:00+00:00",
                "finished_at": "2099-01-01T00:00:00+00:00",
            },
            headers={"X-API-Key": battle["key_a"]},
        )
        assert response.status_code == 200, response.text

        rows = await _submissions(session_maker, battle["id"])
        assert len(rows) == 1
        # The row carries the SERVER's clock, not either value the client sent.
        assert before <= rows[0]["received_at"] <= datetime.now(UTC)
        assert rows[0]["received_at"].year not in (2020, 2099)
        assert rows[0]["finished_at"] is None


class TestSlotDiscipline:
    """seq_no is monotonic, and a side gets one last word."""

    async def test_a_duplicate_seq_no_is_rejected(self, client, db, session_maker, task_id) -> None:
        battle = await _running_battle(db, task_id)
        headers = {"X-API-Key": battle["key_a"]}
        url = f"/api/v1/battles/{battle['id']}/turns"

        assert (
            await client.post(url, json={"content": "first", "seq_no": 1}, headers=headers)
        ).status_code == 200
        clash = await client.post(
            url, json={"content": "overwrite me", "seq_no": 1}, headers=headers
        )

        assert clash.status_code == 409, clash.text
        rows = await _submissions(session_maker, battle["id"])
        assert len(rows) == 1
        assert rows[0]["content"] == "first"  # the original is untouched

    async def test_seq_no_must_move_forward(self, client, db, session_maker, task_id) -> None:
        # Monotonic means monotonic. A checkpoint that arrives with a LOWER
        # number than one already stored is out-of-order: accepting it would let
        # a fighter interleave a rewrite of its own history behind the judge's
        # back, and list_submissions (ORDER BY seq_no) would present it as if it
        # had come first.
        battle = await _running_battle(db, task_id)
        headers = {"X-API-Key": battle["key_a"]}
        url = f"/api/v1/battles/{battle['id']}/turns"

        assert (
            await client.post(url, json={"content": "fifth", "seq_no": 5}, headers=headers)
        ).status_code == 200
        backwards = await client.post(url, json={"content": "sneaky", "seq_no": 3}, headers=headers)

        assert backwards.status_code == 409, backwards.text
        rows = await _submissions(session_maker, battle["id"])
        assert [r["seq_no"] for r in rows] == [5]

    async def test_finalisation_is_one_way_and_idempotent(
        self, client, db, session_maker, task_id
    ) -> None:
        battle = await _running_battle(db, task_id)
        headers = {"X-API-Key": battle["key_a"]}
        url = f"/api/v1/battles/{battle['id']}/turns"

        assert (
            await client.post(
                url, json={"content": "my final", "seq_no": 1, "is_final": True}, headers=headers
            )
        ).status_code == 200

        # A second last word, on a fresh slot: the reconciler may already be
        # judging the first, so this must not land.
        second = await client.post(
            url,
            json={"content": "actually THIS one", "seq_no": 2, "is_final": True},
            headers=headers,
        )
        assert second.status_code == 409, second.text

        rows = await _submissions(session_maker, battle["id"])
        finals = [r for r in rows if r["is_final"]]
        assert len(finals) == 1
        assert finals[0]["content"] == "my final"

    async def test_the_opposing_side_has_its_own_slots(
        self, client, db, session_maker, task_id
    ) -> None:
        # seq_no is per SIDE, not per battle: B reusing A's number is legal.
        battle = await _running_battle(db, task_id)
        url = f"/api/v1/battles/{battle['id']}/turns"

        assert (
            await client.post(
                url, json={"content": "a1", "seq_no": 1}, headers={"X-API-Key": battle["key_a"]}
            )
        ).status_code == 200
        assert (
            await client.post(
                url, json={"content": "b1", "seq_no": 1}, headers={"X-API-Key": battle["key_b"]}
            )
        ).status_code == 200

        rows = await _submissions(session_maker, battle["id"])
        assert {(str(r["side"]), r["seq_no"]) for r in rows} == {("a", 1), ("b", 1)}


class TestBodyLimits:
    """The cap is enforced before the body is stored, not after."""

    async def test_an_oversized_submission_is_rejected_before_it_is_stored(
        self, client, db, session_maker, task_id
    ) -> None:
        battle = await _running_battle(db, task_id)

        response = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "x" * (MAX_SUBMISSION_CHARS + 1), "seq_no": 1},
            headers={"X-API-Key": battle["key_a"]},
        )

        # 422 from the schema: the router never runs, so nothing can be written.
        assert response.status_code == 422, response.text
        assert await _submissions(session_maker, battle["id"]) == []

    async def test_a_submission_at_exactly_the_cap_is_accepted(
        self, client, db, session_maker, task_id
    ) -> None:
        # The boundary is inclusive — an off-by-one here silently truncates an
        # honest fighter's last answer.
        battle = await _running_battle(db, task_id)
        response = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "x" * MAX_SUBMISSION_CHARS, "seq_no": 1},
            headers={"X-API-Key": battle["key_a"]},
        )
        assert response.status_code == 200, response.text

        rows = await _submissions(session_maker, battle["id"])
        assert len(rows[0]["content"]) == MAX_SUBMISSION_CHARS

    async def test_a_negative_seq_no_is_rejected(self, client, db, task_id) -> None:
        battle = await _running_battle(db, task_id)
        response = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "x", "seq_no": -1},
            headers={"X-API-Key": battle["key_a"]},
        )
        assert response.status_code == 422


class TestStatusGate:
    """Only a running battle accepts turns."""

    async def test_a_queued_battle_does_not_accept_turns(
        self, client, db, session_maker, task_id
    ) -> None:
        # Before the shared start there is nothing to answer: a submission here
        # would be judged as if it had been written under the clock.
        repo = BattleRepository(db)
        events = AgentEventRepository(db)
        owner_a, owner_b = await _new_owner(db), await _new_owner(db)
        agent_a, key_a = await _new_agent(db, owner_a)
        agent_b, _key_b = await _new_agent(db, owner_b)

        battle_id = await repo._create_battle(
            task_id=task_id,
            agent_a_id=agent_a,
            agent_a_owner_snapshot=owner_a,
            challenge_ttl_seconds=3600,
            agent_b_id=agent_b,
            agent_b_owner_snapshot=owner_b,
        )
        await repo._mark_accepted(battle_id)
        await repo.reserve_both(battle_id, agent_a, agent_b, 600)
        ev_a = await events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
        ev_b = await events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
        row = await repo.arm_readiness(battle_id, ev_a, ev_b, 60)
        await repo._mark_queued(battle_id, row["readiness_generation"])
        await db.commit()

        response = await client.post(
            f"/api/v1/battles/{battle_id}/turns",
            json={"content": "early", "seq_no": 1},
            headers={"X-API-Key": key_a},
        )
        assert response.status_code == 409
        assert "not accepting turns" in response.json()["detail"]
        assert await _submissions(session_maker, battle_id) == []

    async def test_an_unknown_battle_is_404(self, client, db, task_id) -> None:
        battle = await _running_battle(db, task_id)
        response = await client.post(
            f"/api/v1/battles/{uuid.uuid4()}/turns",
            json={"content": "x", "seq_no": 1},
            headers={"X-API-Key": battle["key_a"]},
        )
        assert response.status_code == 404


async def _running_phase_would_claim(session_maker, battle_id: str) -> bool:
    """Does the reconciler's running phase claim this battle right now?

    Runs the REAL claim_battles_for_reconcile(RUNNING) predicate and rolls back,
    so the probe never mutates the lease it is inspecting. True means the row's
    lease has lapsed and judging happens on the next tick; False means it is
    still fenced by a live lease and would wait out the window.
    """
    async with session_maker() as session:
        claimed = await BattleRepository(session).claim_battles_for_reconcile(
            status=BattleStatus.RUNNING,
            lease_token=str(uuid.uuid4()),
            lease_seconds=BATTLE_LEASE_SECONDS,
            limit=RECONCILE_BATCH,
            max_attempts=RUNNING_MAX_ATTEMPTS,
        )
        await session.rollback()
    return battle_id in {str(b["id"]) for b in claimed}


class TestEarlyFinishThroughTheRoute:
    """The integration point for the early-finish speed-up: POST /turns.

    These are the tests that die if the route stops calling
    expire_running_lease_if_both_final — a repo-level unit test cannot see that
    the route is wired at all.
    """

    async def test_both_finals_via_route_make_the_battle_immediately_claimable(
        self, client, db, session_maker, task_id
    ) -> None:
        battle = await _running_battle(db, task_id)
        # A live lease is in place (deadline_at NOW()+600), so nothing has
        # lapsed on its own — only the route call can make this claimable.
        assert await _running_phase_would_claim(session_maker, battle["id"]) is False

        a_final = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "A answer", "seq_no": 1, "is_final": True},
            headers={"X-API-Key": battle["key_a"]},
        )
        assert a_final.status_code == 200, a_final.text
        # Only one side final yet — still fenced.
        assert await _running_phase_would_claim(session_maker, battle["id"]) is False

        b_final = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "B answer", "seq_no": 1, "is_final": True},
            headers={"X-API-Key": battle["key_b"]},
        )
        assert b_final.status_code == 200, b_final.text
        # Both finals in: the route retired the lease, so the running phase
        # claims it now instead of waiting out BATTLE_LEASE_SECONDS. Removing the
        # route's expire call flips this to False.
        assert await _running_phase_would_claim(session_maker, battle["id"]) is True

    async def test_finals_via_route_survive_a_released_null_lease_without_500(
        self, client, db, session_maker, task_id
    ) -> None:
        # Reproduce finding 1: a normal pre-deadline reconcile poll releases the
        # running row to lease_token=NULL / lease_expires_at=NULL. Writing
        # expires_at=NOW() onto that row would violate battle_lease_token_has_expiry
        # and 500 the fighter's final. The lease_token IS NOT NULL guard skips it.
        battle = await _running_battle(db, task_id)
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE battles SET lease_token = NULL, lease_expires_at = NULL "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle["id"]},
            )
            await session.commit()

        a_final = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "A answer", "seq_no": 1, "is_final": True},
            headers={"X-API-Key": battle["key_a"]},
        )
        b_final = await client.post(
            f"/api/v1/battles/{battle['id']}/turns",
            json={"content": "B answer", "seq_no": 1, "is_final": True},
            headers={"X-API-Key": battle["key_b"]},
        )
        assert a_final.status_code == 200, a_final.text
        assert b_final.status_code == 200, b_final.text

        # Both finals actually persisted — no rollback from a CHECK violation.
        subs = await _submissions(session_maker, battle["id"])
        finals = [s for s in subs if s["is_final"]]
        assert {s["side"] for s in finals} == {"a", "b"}
        # A NULL/NULL running row is already claimable; the guard left it valid.
        assert await _running_phase_would_claim(session_maker, battle["id"]) is True

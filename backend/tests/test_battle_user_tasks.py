"""User-submitted task safety (V70) against real Postgres.

Two invariants, stated so they can be falsified:

    1. AUTHOR EXCLUSION. A task authored by the frozen owner of either fighter
       is not in that battle's binding pool — whether the author owns agent A,
       agent B, or both. The author knows the answer to their own task, so
       binding it would hand them a prepared win at real Elo. LLM validation
       cannot see this (it judges text quality), which is why the defence is a
       pool predicate rather than a review step.

    2. POOL SPLIT. A rated-eligible battle binds only from the approved 'ready'
       pool. A quarantined task — validated but not yet approved by a moderator
       — is bindable ONLY by a battle that cannot move Elo. That is the whole
       point of quarantine: the submission gets real play while the author's
       foreknowledge is worth nothing.

These run the REAL V65-V70 migrations against testcontainers Postgres. Both
invariants are SQL predicates inside lease-fenced data-modifying CTEs, plus a
CHECK constraint; a mock proves none of it.

The control tests matter as much as the negative ones: "nothing ever binds"
would satisfy every negative assertion here while breaking the platform, so each
exclusion test is paired with a case that MUST bind.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from conftest import split_sql_statements
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.repositories.battle_repo import MINIMUM_TASK_POOL, BattleRepository
from app.schemas.battles import TaskSource
from app.services import battle_service as battle_service_module
from app.services.battle_service import BattleService

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
V65_PATH = MIGRATIONS / "V65__agent_events.sql"
V66_PATH = MIGRATIONS / "V66__battles.sql"
V67_PATH = MIGRATIONS / "V67__battle_task_secrecy.sql"
V68_PATH = MIGRATIONS / "V68__battle_anti_abuse.sql"
V69_PATH = MIGRATIONS / "V69__battle_injection_stop_reason.sql"
V70_PATH = MIGRATIONS / "V70__battle_user_tasks.sql"
RUBRIC = [{"criterion": "correctness", "weight": 1.0}]

# The pre-battles tables the migrations build on, mirrored from the other battle
# harnesses: V65+ are additive over these two.
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


class _FakeRedis:
    """The battle service only INCRs/EXPIREs here; no real Redis needed."""

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
        f"{BASE_SCHEMA};{V65_PATH.read_text()};{V66_PATH.read_text()};"
        f"{V67_PATH.read_text()};{V68_PATH.read_text()};"
        f"{V69_PATH.read_text()};{V70_PATH.read_text()}"
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
async def make_user(db):
    async def _make() -> str:
        uid = str(uuid.uuid4())
        await db.execute(
            text("INSERT INTO users (id, email) VALUES (CAST(:id AS UUID), :e)"),
            {"id": uid, "e": f"u-{uid[:8]}@example.test"},
        )
        await db.commit()
        return uid

    return _make


@pytest_asyncio.fixture(loop_scope="module")
async def make_agent(db):
    async def _make(owner: str) -> str:
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
            {"id": aid, "h": f"a{aid[:8]}", "owner": owner},
        )
        await db.commit()
        return aid

    return _make


async def _clear_tasks(db) -> None:
    """Empty the BINDABLE pool so each test controls it completely.

    Retires rather than deletes. Deleting is not available: battles.task_id is
    ON DELETE RESTRICT, and unbinding a battle first is blocked by
    battle_task_binding_all_or_nothing (the snapshot is all-or-nothing) and by
    battle_task_bound_from_queue (a queued battle MUST carry a task). Retiring
    reaches the same end — 'retired' is in no binding pool — without fighting
    constraints that exist for good reason. secret = FALSE belts it, since every
    pool predicate also requires secret = TRUE.
    """
    await db.execute(
        text("UPDATE battle_tasks SET status = 'retired', secret = FALSE")
    )
    await db.commit()


async def _seed_pool(
    db,
    *,
    count: int = MINIMUM_TASK_POOL,
    author: str | None = None,
    status: str = "ready",
    tag: str = "p",
    source: str = "generated",
) -> list[str]:
    """Seed ``count`` DISTINCT-content tasks, all by one author and status.

    Content must be distinct: every pool gate counts COUNT(DISTINCT content_key),
    so duplicated prompts would silently under-fill the pool and a test would
    pass for the wrong reason.

    ``source`` matters as much as ``author``: the anti-cheat excludes a task
    because its author SUBMITTED it, and only a source='user' row carries that
    claim. Seeding an authored task as 'generated' — which every author test did
    before — tested a fixture production never produces: an admin-minted task
    that happens to record its creator. A 'user' row reaching 'ready' also needs
    a moderator, per battle_task_ready_requires_approval, so approval is stamped
    in the same statement that flips the source.
    """
    repo = BattleRepository(db)
    ids = [
        await repo.create_task(
            source=TaskSource.GENERATED,
            title=f"task {tag} {i}",
            prompt=f"Solve puzzle {tag} number {i}.",
            rubric=RUBRIC,
            time_limit_seconds=600,
            category="general",
            difficulty="medium",
            created_by_user_id=author,
        )
        for i in range(count)
    ]
    if status != "ready":
        await db.execute(
            text(
                "UPDATE battle_tasks SET status = :s "
                "WHERE id = ANY(CAST(:ids AS UUID[]))"
            ),
            {"s": status, "ids": ids},
        )
    if source != "generated":
        await db.execute(
            text(
                """
                UPDATE battle_tasks
                SET source = :src,
                    approved_by_user_id = CASE WHEN status = 'ready'
                        THEN CAST(:approver AS UUID) END,
                    approved_at = CASE WHEN status = 'ready' THEN NOW() END
                WHERE id = ANY(CAST(:ids AS UUID[]))
                """
            ),
            {"src": source, "approver": author, "ids": ids},
        )
    await db.commit()
    return ids


async def _reserved_and_acked(db, *, owner_a: str, owner_b: str, make_agent) -> dict:
    """Drive a battle to 'reserved' with both ready-ACKs recorded."""
    svc = BattleService(db)
    agent_a = await make_agent(owner_a)
    agent_b = await make_agent(owner_b)
    battle_id = await svc.create_challenge(
        task_category=None,
        task_difficulty=None,
        agent_a_id=agent_a,
        challenger_owner_user_id=owner_a,
        agent_b_id=agent_b,
    )
    await db.commit()
    assert await svc.accept(battle_id, owner_b) is not None
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


async def _set_rated(db, battle_id: str, value: bool | None) -> None:
    """Pin the frozen rated verdict the pool split reads.

    Set directly rather than driven through the anti-Sybil gate: that gate
    (distinct + verified + aged + within quota) is tested in test_battles_api,
    and reproducing it here would make these tests fail for reasons unrelated to
    the pool split. What is under test is the BINDING predicate's response to
    this column.
    """
    # rated_quota_day moves with it: V68's battle_rated_quota_day_required CHECK
    # ties the two, because a reserved rated slot must name the day whose quota
    # it consumed. Setting the flag alone would produce a row the schema calls
    # impossible.
    await db.execute(
        text(
            """
            UPDATE battles
            SET rated_eligible = :v,
                rated_quota_day = CASE WHEN :v THEN CURRENT_DATE ELSE NULL END,
                rated_ineligibility_reason =
                    CASE WHEN :v THEN NULL ELSE rated_ineligibility_reason END
            WHERE id = CAST(:b AS UUID)
            """
        ),
        {"v": value, "b": battle_id},
    )
    await db.commit()


async def _bind(db, armed: dict) -> dict | None:
    token = await _lease(db, str(armed["id"]))
    bound = await BattleRepository(db).admit_to_queue(
        battle_id=str(armed["id"]),
        readiness_generation=armed["readiness_generation"],
        lease_token=token,
    )
    await db.commit()
    return bound


# --- Invariant 1: author exclusion -----------------------------------------


@pytest.mark.parametrize(
    "author_owns",
    ["a", "b", "both"],
    ids=["author_owns_agent_a", "author_owns_agent_b", "author_owns_both"],
)
async def test_author_never_meets_own_task(db, make_user, make_agent, author_owns):
    """The author's own submissions are not in the pool of a battle they fight.

    The pool is seeded ENTIRELY with the author's tasks, so if the exclusion
    failed the bind would certainly succeed rather than merely being likely to.
    An empty-for-this-battle pool means no candidate, so admit_to_queue returns
    None — the same shape as a genuinely exhausted pool, which is correct: for
    this pair of fighters it IS exhausted.

    All three ownership arrangements are covered because the predicate compares
    against BOTH frozen owner snapshots; a version checking only agent A would
    pass the first case and leak on the second.
    """
    await _clear_tasks(db)
    author = await make_user()
    other = await make_user()
    owner_a = author if author_owns in ("a", "both") else other
    owner_b = author if author_owns in ("b", "both") else other

    # Pool passes the challenge-time gate only because it is seeded before the
    # authorship matters — seed neutral tasks, get to reserved, then swap the
    # pool to the author's. This isolates the BINDING predicate from the
    # challenge-admission gate, which has its own test below.
    await _seed_pool(db, author=None, tag="neutral")
    armed = await _reserved_and_acked(
        db, owner_a=owner_a, owner_b=owner_b, make_agent=make_agent
    )
    await _clear_tasks(db)
    await _seed_pool(
        db, author=author, tag=f"authored-{author_owns}", source="user"
    )

    assert await _bind(db, armed) is None, (
        f"a task authored by the owner of {author_owns} was bindable: "
        "the author knows its answer, so this is a prepared-win hole"
    )


async def test_platform_generated_task_binds_for_its_creators_own_agent(
    db, make_user, make_agent
):
    """A 'generated' task is NOT a submission, so its creator is not its author.

    Admin-minted tasks record whoever ran the mint as created_by_user_id. The
    exclusion keyed on that column alone, so on a platform whose whole catalogue
    was seeded by one admin, that admin's own agents saw an EMPTY pool: live
    production returned 409 "not enough fresh tasks" with 20 ready ones in the
    table (pool_ignoring_author=20, pool_after_author_exclusion=0).

    Same shape as test_author_never_meets_own_task, one difference — source.
    Together the two pin the boundary from both sides.
    """
    await _clear_tasks(db)
    admin = await make_user()
    other = await make_user()
    await _seed_pool(db, author=None, tag="neutral")
    armed = await _reserved_and_acked(
        db, owner_a=admin, owner_b=other, make_agent=make_agent
    )
    await _clear_tasks(db)
    minted = set(await _seed_pool(db, author=admin, tag="minted"))

    bound = await _bind(db, armed)
    assert bound is not None, (
        "a platform-generated task was hidden from its own creator's agent — "
        "an admin-seeded catalogue is then invisible to that admin"
    )
    assert str(bound["task_id"]) in minted


async def test_pool_by_a_third_party_still_binds(db, make_user, make_agent):
    """Control for the three tests above.

    Without this, "admit_to_queue always returns None" would satisfy every
    exclusion assertion while breaking every battle on the platform. Identical
    setup, one difference: the pool's author is nobody's owner.
    """
    await _clear_tasks(db)
    stranger = await make_user()
    owner_a = await make_user()
    owner_b = await make_user()
    await _seed_pool(db, author=None, tag="neutral")
    armed = await _reserved_and_acked(
        db, owner_a=owner_a, owner_b=owner_b, make_agent=make_agent
    )
    await _clear_tasks(db)
    await _seed_pool(db, author=stranger, tag="stranger")

    bound = await _bind(db, armed)
    assert bound is not None, "a third party's tasks must remain bindable"
    assert bound["task_id"] is not None


async def test_challenge_is_refused_when_only_own_tasks_match(
    db, make_user, make_agent
):
    """Author exclusion is counted at the challenge gate, not only at bind.

    Covers sites 1-2 (diagnose_challenge / create_challenge). If the gate
    counted tasks the bind will exclude, it would admit a challenge that can
    never bind — the pair would sit reserved until the challenge expired.
    """
    await _clear_tasks(db)
    author = await make_user()
    await _seed_pool(db, author=author, tag="authored-gate", source="user")
    svc = BattleService(db)
    agent_a = await make_agent(author)
    agent_b = await make_agent(await make_user())

    denial = await BattleRepository(db).diagnose_challenge(
        task_category=None,
        task_difficulty=None,
        agent_a_id=agent_a,
        challenger_owner_user_id=author,
        agent_b_id=agent_b,
        target_cap=10,
        agent_b_owner_snapshot=None,
    )
    assert denial is not None, "the gate must not promise a challenge that cannot bind"
    assert denial.value == "insufficient_task_pool"
    assert svc is not None


# --- Invariant 2: the quarantine pool split --------------------------------


async def test_rated_battle_never_binds_a_quarantined_task(db, make_user, make_agent):
    """THE test the backstop must never be needed for.

    A pool holding both approved and quarantined tasks, bound by a
    rated-eligible battle: the quarantined ones must be invisible. This asserts
    the PRIMARY mechanism (the pool split in admit_to_queue), not the
    settle-time backstop — if this passes, the backstop is dead code by design.
    """
    await _clear_tasks(db)
    owner_a = await make_user()
    owner_b = await make_user()
    await _seed_pool(db, author=None, tag="ready")
    quarantined = set(
        await _seed_pool(db, author=None, status="quarantine", tag="quar")
    )
    armed = await _reserved_and_acked(
        db, owner_a=owner_a, owner_b=owner_b, make_agent=make_agent
    )
    await _set_rated(db, str(armed["id"]), True)

    bound = await _bind(db, armed)
    assert bound is not None, "the approved pool was large enough; this must bind"
    assert str(bound["task_id"]) not in quarantined, (
        "a rated battle bound a quarantined task — its author may have written "
        "it, and this battle moves real Elo"
    )


async def test_rated_battle_with_only_quarantined_tasks_binds_nothing(
    db, make_user, make_agent
):
    """The strict form: no approved task, so a rated battle binds nothing.

    Distinguishes "preferred the ready one" from "cannot see the quarantined
    ones". Without this, a pool split that merely ORDERed by status would pass
    the test above.
    """
    await _clear_tasks(db)
    owner_a = await make_user()
    owner_b = await make_user()
    await _seed_pool(db, author=None, tag="neutral")
    armed = await _reserved_and_acked(
        db, owner_a=owner_a, owner_b=owner_b, make_agent=make_agent
    )
    await _clear_tasks(db)
    await _seed_pool(db, author=None, status="quarantine", tag="quar")
    await _set_rated(db, str(armed["id"]), True)

    assert await _bind(db, armed) is None


async def test_unrated_battle_may_bind_a_quarantined_task(db, make_user, make_agent):
    """Quarantine must still get real play, or submissions never earn approval.

    The mirror of the two tests above: the same pool that a rated battle cannot
    touch is exactly what an unrated one is for.
    """
    await _clear_tasks(db)
    owner_a = await make_user()
    owner_b = await make_user()
    await _seed_pool(db, author=None, tag="neutral")
    armed = await _reserved_and_acked(
        db, owner_a=owner_a, owner_b=owner_b, make_agent=make_agent
    )
    await _clear_tasks(db)
    quarantined = set(
        await _seed_pool(db, author=None, status="quarantine", tag="quar")
    )
    await _set_rated(db, str(armed["id"]), False)

    bound = await _bind(db, armed)
    assert bound is not None, "an unrated battle must be able to play quarantine"
    assert str(bound["task_id"]) in quarantined


async def test_binding_a_quarantined_task_counts_a_quarantine_battle(
    db, make_user, make_agent
):
    """The moderator's evidence must actually accrue.

    quarantine_battles is what an approval decision is made on, so a counter
    that never moves would make the moderation queue a list of zeros.
    """
    await _clear_tasks(db)
    owner_a = await make_user()
    owner_b = await make_user()
    await _seed_pool(db, author=None, tag="neutral")
    armed = await _reserved_and_acked(
        db, owner_a=owner_a, owner_b=owner_b, make_agent=make_agent
    )
    await _clear_tasks(db)
    await _seed_pool(db, author=None, status="quarantine", tag="quar")
    await _set_rated(db, str(armed["id"]), False)

    bound = await _bind(db, armed)
    assert bound is not None
    count = (
        await db.execute(
            text(
                "SELECT quarantine_battles FROM battle_tasks "
                "WHERE id = CAST(:t AS UUID)"
            ),
            {"t": str(bound["task_id"])},
        )
    ).scalar_one()
    assert count == 1


# --- Structural guarantees from the migration ------------------------------


async def test_unapproved_user_task_cannot_be_ready(db, make_user):
    """The CHECK, not the code, is what keeps unapproved work out of the pool.

    A code path can forget a branch; this cannot. Asserted directly against the
    constraint so a future service method cannot quietly promote a submission.
    """
    await _clear_tasks(db)
    author = await make_user()
    with pytest.raises(Exception, match="battle_task_ready_requires_approval"):
        await db.execute(
            text(
                """
                INSERT INTO battle_tasks
                    (source, title, prompt, rubric, category, difficulty, status,
                     created_by_user_id)
                VALUES ('user', 't', 'unapproved but ready', '["a"]'::jsonb,
                        'general', 'easy', 'ready', CAST(:u AS UUID))
                """
            ),
            {"u": author},
        )
    await db.rollback()


async def test_user_submissions_dedup_on_canonical_content(db, make_user):
    """Case and whitespace variants of one submission are ONE submission.

    The canonical key is the same expression battle_repo bins the pool by, so a
    resubmission with different spacing cannot slip past dedup.
    """
    await _clear_tasks(db)
    author = await make_user()
    repo = BattleRepository(db)
    original = await repo.create_task(
        source=TaskSource.GENERATED,
        title="orig",
        prompt="Write a Parser for logs.",
        rubric=RUBRIC,
        time_limit_seconds=600,
        category="general",
        difficulty="medium",
        created_by_user_id=author,
    )
    # Scoped to this row by id: a blanket UPDATE would also relabel the retired
    # tasks other tests left behind, and their content keys would then collide
    # inside the user-scoped unique index for reasons unrelated to this test.
    await db.execute(
        text(
            "UPDATE battle_tasks SET source = 'user', status = 'quarantine' "
            "WHERE id = CAST(:t AS UUID)"
        ),
        {"t": original},
    )
    await db.commit()

    with pytest.raises(Exception, match="idx_battle_tasks_user_content_key_unique"):
        await db.execute(
            text(
                """
                INSERT INTO battle_tasks
                    (source, title, prompt, rubric, category, difficulty, status,
                     created_by_user_id)
                VALUES ('user', 'dupe', '  write a   PARSER for LOGS.  ',
                        '["a"]'::jsonb, 'general', 'medium', 'pending_validation',
                        CAST(:u AS UUID))
                """
            ),
            {"u": author},
        )
    await db.rollback()

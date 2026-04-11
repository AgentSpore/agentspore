"""Integration test for councils — full lifecycle against testcontainers PG.

Mocks PureLLMAdapter.generate so the state machine runs without calling OpenRouter,
then asserts: council reaches `done`, all panelists voted, resolution artifact is
written, consensus score is present, messages table has brief + discussion + votes +
resolution rows.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

try:
    from testcontainers.postgres import PostgresContainer
    _HAS_TC = True
except Exception:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(not _HAS_TC, reason="testcontainers not installed")


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def pg_async_url(pg_container):
    raw = pg_container.get_connection_url()
    return raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture
async def session_maker(pg_async_url):
    """Create the council schema + bind a session_maker to the test PG."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(pg_async_url, future=True)
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto";'))
        # Minimal slice — councils + panelists + messages + votes, no FKs to agents/users.
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS councils (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                topic TEXT NOT NULL,
                brief TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'round_robin',
                status TEXT NOT NULL DEFAULT 'convening',
                current_round INT NOT NULL DEFAULT 0,
                max_rounds INT NOT NULL DEFAULT 3,
                max_tokens_per_msg INT NOT NULL DEFAULT 500,
                timebox_seconds INT NOT NULL DEFAULT 600,
                panel_size INT NOT NULL DEFAULT 5,
                convener_user_id UUID,
                convener_agent_id UUID,
                convener_ip TEXT,
                is_public BOOLEAN NOT NULL DEFAULT TRUE,
                resolution TEXT,
                consensus_score REAL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                started_at TIMESTAMPTZ,
                ended_at TIMESTAMPTZ
            );
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS council_panelists (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                council_id UUID NOT NULL REFERENCES councils(id) ON DELETE CASCADE,
                adapter TEXT NOT NULL,
                agent_id UUID,
                model_id TEXT,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'panelist',
                perspective TEXT,
                joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_spoke_round INT
            );
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS council_messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                council_id UUID NOT NULL REFERENCES councils(id) ON DELETE CASCADE,
                panelist_id UUID REFERENCES council_panelists(id) ON DELETE SET NULL,
                round_num INT NOT NULL DEFAULT 0,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                meta JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS council_votes (
                council_id UUID NOT NULL REFERENCES councils(id) ON DELETE CASCADE,
                panelist_id UUID NOT NULL REFERENCES council_panelists(id) ON DELETE CASCADE,
                vote TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                reasoning TEXT,
                voted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (council_id, panelist_id)
            );
        """))

    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_council_lifecycle_with_mocked_adapter(session_maker, monkeypatch):
    """Run a 3-panelist, 2-round council end to end, mocking PureLLMAdapter."""
    from app.repositories.council_repo import CouncilRepository
    from app.services import council_service as cs

    # Redirect service's async_session_maker to the test one.
    monkeypatch.setattr(cs, "async_session_maker", session_maker)

    # Scripted panelist responses: round 1, round 2, vote. Cycled per panelist.
    script = {
        "Alice": [
            "First point: correctness before speed.",
            "Refining: we need property tests.",
            '{"vote": "approve", "confidence": 0.8, "reasoning": "Plan is sound"}',
        ],
        "Bob": [
            "Counterpoint: speed matters too.",
            "OK but benchmarks first.",
            '{"vote": "approve", "confidence": 0.6, "reasoning": "Acceptable risk"}',
        ],
        "Devil": [
            "What about rollback? No one mentioned it.",
            "Still no rollback plan. This will burn us.",
            '{"vote": "reject", "confidence": 0.9, "reasoning": "Rollback missing"}',
        ],
    }
    call_count = {"Alice": 0, "Bob": 0, "Devil": 0}

    async def fake_generate(self, system_prompt, messages):
        name = self.panelist["display_name"]
        i = call_count[name]
        call_count[name] += 1
        content = script[name][i]
        return {"content": content, "meta": {"mocked": True}}

    monkeypatch.setattr(cs.PureLLMAdapter, "generate", fake_generate)

    # Seed council + panelists directly via repo.
    async with session_maker() as session:
        repo = CouncilRepository(session)
        council = await repo.create(
            topic="Migrate to async workers?",
            brief="We are considering moving the job queue to an async model.",
            mode="round_robin",
            panel_size=3,
            max_rounds=2,
            max_tokens_per_msg=300,
            timebox_seconds=60,
            is_public=True,
        )
        await session.commit()
        cid = str(council["id"])

        for name, role, persp in [
            ("Alice", "panelist", "You value correctness"),
            ("Bob", "panelist", "You value performance"),
            ("Devil", "devil_advocate", "Find what is missing"),
        ]:
            await repo.add_panelist(
                cid, adapter="pure_llm", display_name=name,
                role=role, model_id=f"fake/{name.lower()}:free", perspective=persp,
            )
        await session.commit()

    # Drive the state machine inline (not as a background task, so we can assert on results).
    await cs.run_council(cid)

    async with session_maker() as session:
        repo = CouncilRepository(session)
        final = await repo.get_by_id(cid)
        messages = await repo.list_messages(cid)
        votes = await repo.list_votes(cid)
        panelists = await repo.list_panelists(cid)

    assert final["status"] == "done"
    assert final["ended_at"] is not None
    assert final["resolution"] is not None
    assert "Council resolution" in final["resolution"]
    assert final["consensus_score"] is not None

    # 2 approve + 1 reject → score ~ (0.8 + 0.6 - 0.9) / 3 ≈ 0.167
    assert final["consensus_score"] == pytest.approx((0.8 + 0.6 - 0.9) / 3, abs=0.01)

    # Message rows: 1 brief + (3 panelists × 2 rounds = 6) discussion + 1 vote_call
    # + 3 vote messages + 1 resolution = 12
    kinds = [m["kind"] for m in messages]
    assert kinds.count("brief") == 1
    assert kinds.count("vote_call") == 1
    assert kinds.count("resolution") == 1
    assert kinds.count("message") == 9  # 6 discussion + 3 vote messages

    # Votes recorded for all panelists
    assert len(votes) == 3
    vote_map = {str(v["panelist_id"]): v["vote"] for v in votes}
    name_by_pid = {str(p["id"]): p["display_name"] for p in panelists}
    assert vote_map[[pid for pid, n in name_by_pid.items() if n == "Alice"][0]] == "approve"
    assert vote_map[[pid for pid, n in name_by_pid.items() if n == "Devil"][0]] == "reject"

    # Resolution mentions all panelists
    for name in ("Alice", "Bob", "Devil"):
        assert name in final["resolution"]

    # Each panelist spoke in each round
    for p in panelists:
        assert p["last_spoke_round"] == 2


@pytest.mark.asyncio
async def test_malformed_vote_defaults_to_abstain(session_maker, monkeypatch):
    """If a panelist returns prose instead of JSON for the vote, fall back to abstain."""
    from app.repositories.council_repo import CouncilRepository
    from app.services import council_service as cs

    monkeypatch.setattr(cs, "async_session_maker", session_maker)

    responses = iter([
        "discussion round one",
        "I think we should go with approve, but I'm not fully sure.",  # malformed vote
    ])

    async def fake_generate(self, system_prompt, messages):
        return {"content": next(responses), "meta": {}}

    monkeypatch.setattr(cs.PureLLMAdapter, "generate", fake_generate)

    async with session_maker() as session:
        repo = CouncilRepository(session)
        council = await repo.create(
            topic="x", brief="y", mode="round_robin",
            panel_size=1, max_rounds=1, max_tokens_per_msg=100, timebox_seconds=60,
        )
        await session.commit()
        cid = str(council["id"])
        await repo.add_panelist(cid, adapter="pure_llm", display_name="Solo", model_id="fake/s:free")
        await session.commit()

    await cs.run_council(cid)

    async with session_maker() as session:
        repo = CouncilRepository(session)
        votes = await repo.list_votes(cid)
        final = await repo.get_by_id(cid)

    assert len(votes) == 1
    assert votes[0]["vote"] == "abstain"
    assert final["status"] == "done"
    # Abstain contributes 0 to the score
    assert final["consensus_score"] == pytest.approx(0.0, abs=0.01)

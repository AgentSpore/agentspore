"""S1: platform-owned static services are seeded by V99 and hidden from agent rankings."""

from pathlib import Path

import pytest
from sqlalchemy import text
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world

from app.repositories.agent_repo import AgentRepository

MIGRATION = Path(__file__).resolve().parents[2] / "db/migrations/V99__platform_services.sql"
SERVICES = {
    "foundphone": (None, "1daa40598ada460291c819a55968ce55"),
    "family-safety": ("family_safety", "5c97579aaa714f53aa63ce800f93979e"),
    "emergency-binder": ("emergency_binder", "ccb56a65f4844a63a6b14756fe500537"),
}


async def snapshot(maker) -> dict:
    async with maker.begin() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT r.handle, replace(r.active_revision::text, '-', '') AS revision, "
                    "p.metrics_profile, p.status, p.repo_url, p.deploy_url "
                    "FROM service_routes r JOIN projects p ON p.id = r.project_id "
                    "WHERE r.handle = ANY(:handles)"
                ),
                {"handles": list(SERVICES)},
            )
        ).mappings()
        return {row["handle"]: dict(row) for row in rows}


async def test_seed_registers_three_services_with_fixed_revisions(usage_world):
    seeded = await snapshot(usage_world.maker)
    assert set(seeded) == set(SERVICES)
    for handle, (profile, revision) in SERVICES.items():
        row = seeded[handle]
        assert (row["metrics_profile"], row["revision"], row["status"]) == (
            profile,
            revision,
            "deployed",
        )
        assert row["deploy_url"] == f"https://{handle}.agentspore.com"
        assert row["repo_url"].endswith(f"/services/{handle}")
    async with usage_world.maker.begin() as db:
        reserved = (await db.execute(text("SELECT count(*) FROM service_hostnames"))).scalar_one()
    assert reserved >= 3


async def test_system_agent_is_inactive_keyless_and_owns_the_projects(usage_world):
    async with usage_world.maker.begin() as db:
        agent = (
            (
                await db.execute(
                    text(
                        "SELECT a.is_active, a.api_key_hash, a.owner_user_id, a.agent_type, "
                        "(SELECT count(*) FROM projects p "
                        "WHERE p.creator_agent_id = a.id) AS owned "
                        "FROM agents a WHERE a.handle = 'agentspore-services'"
                    )
                )
            )
            .mappings()
            .one()
        )
    assert agent["is_active"] is False
    assert agent["api_key_hash"] is None
    assert agent["owner_user_id"] is None
    assert agent["agent_type"] == "internal"
    assert agent["owned"] == 3


async def test_migration_rerun_is_idempotent(usage_world):
    before = await snapshot(usage_world.maker)
    async with usage_world.maker.kw["bind"].begin() as conn:
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(MIGRATION.read_text())
    assert await snapshot(usage_world.maker) == before
    async with usage_world.maker.begin() as db:
        counts = (
            await db.execute(
                text(
                    "SELECT (SELECT count(*) FROM agents WHERE handle = 'agentspore-services'), "
                    "(SELECT count(*) FROM service_routes WHERE handle = ANY(:h))"
                ),
                {"h": list(SERVICES)},
            )
        ).one()
    assert tuple(counts) == (1, 3)


async def test_leaderboard_excludes_the_inactive_system_agent(usage_world):
    async with usage_world.maker.begin() as db:
        rows = await AgentRepository(db).get_leaderboard("karma DESC", None, 100)
    handles = {row["handle"] for row in rows}
    assert "agentspore-services" not in handles
    assert {"a2-0", "a2-1"} <= handles


async def test_platform_stats_count_services_but_not_the_system_agent(usage_world):
    async with usage_world.maker.begin() as db:
        stats = await AgentRepository(db).get_platform_stats()
    assert stats["total_agents"] == 2
    assert stats["projects_deployed"] == 3
    assert stats["total_projects"] >= 3 + 3


@pytest.mark.parametrize("handle", ["foundphone", "family-safety", "emergency-binder"])
async def test_seeded_hostnames_are_immutable(usage_world, handle):
    async with usage_world.maker.begin() as db:
        with pytest.raises(Exception, match="immutable"):
            await db.execute(
                text("DELETE FROM service_hostnames WHERE handle = :handle"), {"handle": handle}
            )

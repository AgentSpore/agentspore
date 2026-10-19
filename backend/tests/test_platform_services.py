"""S1: platform-owned static services are seeded by V99 and hidden from agent rankings."""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world

from app.repositories.agent_repo import AgentRepository
from app.repositories.deployment_repo import DeploymentConflictError, DeploymentRepository
from app.repositories.webhook_repo import WebhookRepository
from app.schemas.deployments import DeploymentEnqueue

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


@pytest.fixture
async def revoked_agent(usage_world):
    async with usage_world.maker.begin() as db:
        await db.execute(
            text("UPDATE agents SET is_active = FALSE WHERE handle = 'a2-1'"),
        )


async def test_revoked_external_agent_stays_in_rankings_and_totals(usage_world, revoked_agent):
    async with usage_world.maker.begin() as db:
        repo = AgentRepository(db)
        handles = {row["handle"] for row in await repo.get_leaderboard("karma DESC", None, 100)}
        stats = await repo.get_platform_stats()
    assert {"a2-0", "a2-1"} <= handles
    assert stats["total_agents"] == 2


@pytest.fixture
async def older_agent_repos(usage_world):
    async with usage_world.maker.begin() as db:
        for handle in SERVICES:
            await db.execute(
                text(
                    "INSERT INTO projects (id, title, creator_agent_id, repo_url, created_at) "
                    "VALUES (gen_random_uuid(), :title, :agent, :url, now() - interval '1 day')"
                ),
                {
                    "title": "agent " + handle,
                    "agent": usage_world.agents[0],
                    "url": "https://github.com/some-org/" + handle,
                },
            )


@pytest.mark.parametrize("handle", list(SERVICES))
@pytest.mark.parametrize("provider", [None, "github"])
async def test_webhook_lookup_never_resolves_seeded_services(
    usage_world, older_agent_repos, handle, provider
):
    async with usage_world.maker.begin() as db:
        found = await WebhookRepository(db).find_project_by_repo_slug(handle, provider)
        seeded = set(
            (
                await db.execute(
                    text(
                        "SELECT original_project_id FROM service_hostnames WHERE handle = ANY(:h)"
                    ),
                    {"h": list(SERVICES)},
                )
            ).scalars()
        )
    assert found is not None
    assert found["id"] not in seeded
    assert found["title"] == "agent " + handle


@pytest.mark.parametrize("handle", list(SERVICES))
async def test_webhook_lookup_finds_nothing_when_only_the_seeded_service_matches(
    usage_world, handle
):
    async with usage_world.maker.begin() as db:
        assert await WebhookRepository(db).find_project_by_repo_slug(handle) is None


async def test_github_sync_selection_excludes_seeded_services(usage_world):
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "UPDATE projects SET repo_url = 'https://github.com/o/a2', vcs_provider = 'github' "
                "WHERE id = :id"
            ),
            {"id": usage_world.projects[0]},
        )
        titles = {row["id"] for row in await WebhookRepository(db).list_github_sync_projects()}
        seeded = set(
            (
                await db.execute(
                    text(
                        "SELECT original_project_id FROM service_hostnames WHERE handle = ANY(:h)"
                    ),
                    {"h": list(SERVICES)},
                )
            ).scalars()
        )
    assert titles == {usage_world.projects[0]}
    assert not titles & seeded


async def test_deployment_enqueue_rejects_platform_service_projects(usage_world):
    async with usage_world.maker.begin() as db:
        seeded = (
            await db.execute(
                text(
                    "SELECT original_project_id FROM service_hostnames WHERE handle = 'foundphone'"
                )
            )
        ).scalar_one()
        request = DeploymentEnqueue(
            project_id=seeded,
            idempotency_key=uuid4(),
            commit_sha="a" * 40,
            repository_url="https://github.com/o/r",
        )
        with pytest.raises(DeploymentConflictError, match="platform_service"):
            await DeploymentRepository(db).enqueue(request)
        ordinary = request.model_copy(update={"project_id": usage_world.projects[0]})
        assert await DeploymentRepository(db).enqueue(ordinary) is not None

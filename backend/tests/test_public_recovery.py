"""Recovery acceptance against actual PostgreSQL migrations and native readers."""

from argparse import Namespace
from uuid import uuid4

import pytest
from app import recover_public_catalog as recovery_cli
from app.repositories import activity_repo, project_repo
from app.repositories.agent_repo import AgentRepository
from app.schemas.public_recovery import RecoveryManifest
from app.services.agent_service import AgentService
from app.services.public_recovery_service import recover_public_catalog
from sqlalchemy import text
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world


@pytest.fixture
def recovery_manifest():
    """Synthetic values follow the real projected public manifest's schema."""
    url = "https://github.com/AgentSpore/thoughtpeer"
    return RecoveryManifest.model_validate(
        {
            "version": 1,
            "source": "github_public_history",
            "observed_on": "2026-10-21",
            "agents": [
                {
                    "id": str(uuid4()),
                    "name": "Synthetic historical agent",
                    "source": "retained_local_mapping",
                    "verified": False,
                    "source_sha256": "0" * 64,
                }
            ],
            "projects": [
                {
                    "id": str(uuid4()),
                    "id_kind": "new_catalog_id",
                    "slug": "thoughtpeer",
                    "url": url,
                    "description": "Synthetic public metadata",
                    "created_at": "2026-01-01T00:00:00Z",
                    "pushed_at": None,
                    "history_total": 3,
                    "branch": "main",
                    "commits": [
                        {
                            "sha": "a" * 40,
                            "created_at": "2026-01-02T00:00:00Z",
                            "url": url + "/commit/" + "a" * 40,
                            "subject": "Synthetic commit",
                        }
                    ],
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_unknown_creator_project_remains_public(usage_world):
    project_id = uuid4()
    async with usage_world.maker.begin() as db:
        await db.execute(
            text("INSERT INTO projects (id,title,creator_agent_id) VALUES (:id,'Recovered',NULL)"),
            {"id": project_id},
        )
        project = await project_repo.get_project_by_id(db, project_id)
        assert project is not None
        assert project["creator_agent_id"] is None
        assert project["agent_name"] == "Unknown author"


@pytest.mark.asyncio
async def test_replay_keeps_real_history_inactive_identity_and_no_economy(
    usage_world, recovery_manifest
):
    async with usage_world.maker.begin() as db:
        first = await recover_public_catalog(db, recovery_manifest)
        second = await recover_public_catalog(db, recovery_manifest)
        assert first == {"agents_inserted": 1, "projects_inserted": 1, "commits_inserted": 1}
        assert second == dict.fromkeys(first, 0)
        agent = (
            (
                await db.execute(
                    text("SELECT * FROM agents WHERE id=:id"),
                    {"id": recovery_manifest.agents[0].id},
                )
            )
            .mappings()
            .one()
        )
        assert agent["api_key_hash"] is None and agent["owner_user_id"] is None
        assert agent["owner_email"] is None and agent["last_heartbeat"] is None
        assert agent["is_active"] is False
        profile = AgentService._agent_profile(dict(agent))
        assert profile.created_at is None and profile.model_name is None
        assert profile.model_provider is None and profile.dna_risk is None
        assert profile.dna_speed is None and profile.dna_creativity is None
        assert profile.dna_verbosity is None
        assert profile.recovery_provenance is not None
        assert profile.recovery_provenance["source"] == "retained_local_mapping"
        assert profile.recovery_provenance["verified"] is False
        totals = (
            await db.execute(
                text(
                    "SELECT SUM(karma), SUM(code_commits), "
                    "(SELECT COUNT(*) FROM project_contributors) FROM agents"
                )
            )
        ).one()
        assert tuple(totals) == (0, 0, 0)
        events = await activity_repo.get_activity_events(db, limit=400)
        assert len(events) == 1 and all(e["agent_id"] is None for e in events)
        by_sha = {e["metadata"]["commit_sha"]: e for e in events}
        commit = recovery_manifest.projects[0].commits[0]
        assert by_sha[commit.sha]["metadata"]["github_url"] == commit.url
        assert by_sha[commit.sha]["ts"] == str(commit.created_at)
        duplicate = await db.execute(
            text("""
            INSERT INTO agent_activity (agent_id,project_id,action_type,description,metadata)
            SELECT NULL,project_id,action_type,description,metadata FROM agent_activity
            WHERE metadata->>'commit_sha'=:sha
            ON CONFLICT DO NOTHING RETURNING id
        """),
            {"sha": commit.sha},
        )
        assert duplicate.first() is None
        projects = await project_repo.list_projects(db, limit=100)
        recovered = [p for p in projects if p.get("recovery_provenance")]
        assert len(recovered) == 1 and all(p["creator_agent_id"] is None for p in recovered)


@pytest.mark.asyncio
async def test_existing_repository_identity_fields_are_preserved(usage_world, recovery_manifest):
    project = recovery_manifest.projects[0]
    old_id = usage_world.projects[0]
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "UPDATE projects SET repo_url=:url, description='Preserve', votes_up=7 WHERE id=:id"
            ),
            {"url": project.url.upper() + ".GIT/", "id": old_id},
        )
        await recover_public_catalog(db, recovery_manifest)
        stored = await project_repo.get_project_by_id(db, old_id)
        assert stored is not None
        assert stored["description"] == "Preserve" and stored["votes_up"] == 7
        assert stored["creator_agent_id"] == str(usage_world.agents[0])
        assert stored["recovery_provenance"] is None
        assert await project_repo.get_project_by_id(db, project.id) is None


@pytest.mark.asyncio
async def test_identity_collision_rolls_back_whole_import(usage_world, recovery_manifest):
    project = recovery_manifest.projects[0]
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO projects (id,title,creator_agent_id) VALUES (:id,'Unrelated',:agent)"
            ),
            {"id": project.id, "agent": usage_world.agents[0]},
        )
    with pytest.raises(ValueError, match="Historical project identity collision"):
        async with usage_world.maker.begin() as db:
            await recover_public_catalog(db, recovery_manifest)
    async with usage_world.maker() as db:
        assert (
            await db.execute(
                text("SELECT COUNT(*) FROM agents WHERE recovery_provenance IS NOT NULL")
            )
        ).scalar_one() == 0


@pytest.mark.asyncio
async def test_cli_default_rolls_back_and_explicit_apply_commits(
    usage_world, monkeypatch, recovery_manifest, tmp_path
):
    monkeypatch.setattr(recovery_cli, "async_session_maker", usage_world.maker)
    manifest = tmp_path / "synthetic-public-manifest.json"
    manifest.write_text(recovery_manifest.model_dump_json())
    args = Namespace(manifest=manifest, apply=False)
    assert (await recovery_cli.run(args))["status"] == "dry_run_rolled_back"
    async with usage_world.maker() as db:
        assert (
            await db.execute(
                text("SELECT COUNT(*) FROM projects WHERE recovery_provenance IS NOT NULL")
            )
        ).scalar_one() == 0
    args.apply = True
    assert (await recovery_cli.run(args))["status"] == "applied"
    async with usage_world.maker() as db:
        assert (
            await db.execute(
                text("SELECT COUNT(*) FROM projects WHERE recovery_provenance IS NOT NULL")
            )
        ).scalar_one() == 1


@pytest.mark.asyncio
async def test_global_commit_count_includes_unknown_history_without_double_credit(
    usage_world, recovery_manifest
):
    async with usage_world.maker.begin() as db:
        repo = AgentRepository(db)
        agent = usage_world.agents[0]
        project = recovery_manifest.projects[0]
        await db.execute(
            text("UPDATE agents SET code_commits=2,karma=37 WHERE id=:id"), {"id": agent}
        )
        await repo.record_commit_shas(agent, ["b" * 40, "c" * 40], usage_world.projects[0])
        await db.execute(
            text("""
            INSERT INTO agent_activity (agent_id,project_id,action_type,description,metadata)
            SELECT NULL,CASE WHEN kind='orphan' THEN NULL ELSE CAST(:pid AS uuid) END,
                CASE WHEN kind='review' THEN 'code_review' ELSE 'code_commit' END,
                'Synthetic ignored row',jsonb_build_object(
                    'source',CASE WHEN kind='source' THEN 'other' ELSE 'github_public_history' END,
                    'commit_sha',CASE WHEN kind='sha' THEN 'short' ELSE repeat('d',40) END)
            FROM (VALUES ('source'),('sha'),('review'),('orphan')) AS fixture(kind)
        """),
            {"pid": usage_world.projects[0]},
        )
        assert (await repo.get_platform_stats())["total_code_commits"] == 2
        await recover_public_catalog(db, recovery_manifest)
        assert (await repo.get_platform_stats())["total_code_commits"] == 3
        await recover_public_catalog(db, recovery_manifest)
        assert (await repo.get_platform_stats())["total_code_commits"] == 3
        await repo.record_commit_shas(agent, [project.commits[0].sha], project.id)
        await db.execute(
            text("UPDATE agents SET code_commits=code_commits+1 WHERE id=:id"), {"id": agent}
        )
        assert (await repo.get_platform_stats())["total_code_commits"] == 3
        counters = (
            await db.execute(
                text("SELECT code_commits,karma FROM agents WHERE id=:id"), {"id": agent}
            )
        ).one()
        assert tuple(counters) == (3, 37)

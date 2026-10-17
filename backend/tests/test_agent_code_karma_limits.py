"""A3 caps code/file rewards across real producers without capping other karma."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from test_agent_registration_limits import a3_db as a3_db
from test_agent_registration_limits import a3_pg as a3_pg

from app.core.background import GitHubSyncTask
from app.repositories.agent_repo import AgentRepository
from app.repositories.code_karma_repo import CodeKarmaRepository
from app.repositories.webhook_repo import WebhookRepository
from app.services.agent_service import AgentService
from app.services.webhook_service import GitHubWebhookService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def karma_subject(a3_db):
    """Use actual migrated identity/project columns and unrelated historical karma."""
    agent_id, project_id, owner_id = uuid4(), uuid4(), uuid4()
    async with a3_db.begin() as db:
        await db.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, name) "
                "VALUES (:id, 'a3-owner@example.test', '!', 'Synthetic owner')"
            ),
            {"id": owner_id},
        )
        await db.execute(
            text(
                "INSERT INTO agents (id, name, handle, github_user_login, owner_user_id, karma) "
                "VALUES (:id, 'A3 contributor', 'a3-contributor', 'a3-contributor', :owner, 17)"
            ),
            {"id": agent_id, "owner": owner_id},
        )
        await db.execute(
            text(
                "INSERT INTO projects (id, title, creator_agent_id, repo_url) "
                "VALUES (:id, 'A3 project', :agent, 'https://github.invalid/org/a3-project')"
            ),
            {"id": project_id, "agent": agent_id},
        )
    return a3_db, agent_id, project_id


@pytest.mark.parametrize(
    "writers",
    [
        (AgentRepository,),
        (WebhookRepository,),
        (AgentRepository, WebhookRepository, "file"),
    ],
)
async def test_code_and_file_producers_share_fifty_points_preserve_commit_count(
    karma_subject,
    writers,
) -> None:
    """Real award methods must cap their shared delta, preserving other karma."""
    maker, agent_id, project_id = karma_subject
    accepted = 0
    async with maker.begin() as db:
        for index in range(12):
            writer = writers[index % len(writers)]
            if writer == "file":
                await AgentService(db)._audit_and_karma(
                    {"id": agent_id},
                    project_id,
                    "PUT",
                    "/contents",
                    200,
                )
            else:
                count = await AgentRepository(db).record_commit_shas(
                    agent_id,
                    [f"{index + 1:040x}"],
                    project_id=project_id,
                )
                await writer(db).increment_commits_and_karma(agent_id, count)
                accepted += count
        row = (
            await db.execute(
                text("SELECT karma, code_commits FROM agents WHERE id = :id"), {"id": agent_id}
            )
        ).one()
        assert row.code_commits == accepted
        assert row.karma - 17 == 50


async def test_concurrent_mixed_awards_share_remaining_budget(karma_subject) -> None:
    """Separate connections contest the final ten points after forty were earned."""
    maker, agent_id, project_id = karma_subject
    async with maker.begin() as db:
        count = await AgentRepository(db).record_commit_shas(
            agent_id,
            [f"{index + 1:040x}" for index in range(4)],
            project_id=project_id,
        )
        await AgentRepository(db).increment_commits_and_karma(agent_id, count)
    barrier, pids = asyncio.Barrier(12), []

    async def award(index):
        async with maker.begin() as db:
            pids.append(await db.scalar(text("SELECT pg_backend_pid()")))
            count = 0
            if index % 3 != 2:
                count = await AgentRepository(db).record_commit_shas(
                    agent_id,
                    [f"{index + 100:040x}"],
                    project_id=project_id,
                )
            await asyncio.wait_for(barrier.wait(), timeout=20)
            if index % 3 == 2:
                await AgentService(db)._audit_and_karma(
                    {"id": agent_id},
                    project_id,
                    "PUT",
                    "/contents/app.py",
                    200,
                )
            else:
                writer = AgentRepository if index % 3 == 0 else WebhookRepository
                await writer(db).increment_commits_and_karma(agent_id, count)

    async with asyncio.TaskGroup() as group:
        for index in range(12):
            group.create_task(award(index))
    assert len(set(pids)) == 12
    async with maker() as db:
        row = (
            await db.execute(
                text("SELECT karma, code_commits FROM agents WHERE id = :id"), {"id": agent_id}
            )
        ).one()
        assert row.code_commits == 12
        assert row.karma - 17 == 50


async def test_empty_commit_flood_then_real_commits_obeys_code_cap(karma_subject) -> None:
    """Empty commits give zero; six nonempty commits must still expose the cap."""
    maker, agent_id, project_id = karma_subject
    payload = {
        "repository": {"full_name": "org/a3-project"},
        "sender": {"login": "a3-contributor", "type": "User"},
        "ref": "refs/heads/main",
        "commits": [{"id": f"{index:040x}", "added": [], "modified": []} for index in range(100)],
    }
    async with maker.begin() as db:
        service = GitHubWebhookService(db)
        result = await service.handle("push", payload)
        assert result["files"] == 0
        row = (
            await db.execute(
                text("SELECT karma, code_commits FROM agents WHERE id = :id"), {"id": agent_id}
            )
        ).one()
        assert (row.karma, row.code_commits) == (17, 0)
        for index in range(6):
            payload["commits"] = [{"id": f"{index + 1000:040x}", "modified": ["app.py"]}]
            await service.handle("push", payload)
        row = (
            await db.execute(
                text("SELECT karma, code_commits FROM agents WHERE id = :id"), {"id": agent_id}
            )
        ).one()
        assert row.code_commits == 6
        assert row.karma - 17 == 50
        assert (
            await db.scalar(
                text(
                    "SELECT contribution_points FROM project_contributors "
                    "WHERE project_id = :project AND agent_id = :agent"
                ),
                {"project": project_id, "agent": agent_id},
            )
            == 60
        )


async def test_previous_week_partial_remainder_and_noncode_rewards(karma_subject):
    """Previous weeks are independent; file rewards take the partial remainder."""
    maker, agent_id, project_id = karma_subject
    async with maker.begin() as db:
        await db.execute(
            text("""
            INSERT INTO agent_code_karma_limits (agent_id, week_start, awarded)
            VALUES (:id, date_trunc('week', statement_timestamp() AT TIME ZONE 'UTC')::date - 7, 50)
        """),
            {"id": agent_id},
        )
        await AgentRepository(db).increment_commits_and_karma(agent_id, 4)
        service = AgentService(db)
        for _ in range(3):
            await service._audit_and_karma(
                {"id": agent_id}, project_id, "DELETE", "/contents/a.py", 200
            )
        await service._audit_and_karma({"id": agent_id}, project_id, "PUT", "/contents/a.py", 200)
        await service._audit_and_karma({"id": agent_id}, project_id, "POST", "/issues", 200)
        await service._audit_and_karma({"id": agent_id}, project_id, "PUT", "/contents/a.py", 500)
        row = (
            await db.execute(
                text("SELECT karma, code_commits FROM agents WHERE id=:id"), {"id": agent_id}
            )
        ).one()
        assert (row.karma, row.code_commits) == (72, 4)
        assert (
            await db.scalar(
                text("SELECT sum(awarded) FROM agent_code_karma_limits WHERE agent_id=:id"),
                {"id": agent_id},
            )
            == 100
        )


async def test_rollback_refunds_budget_and_deduplicated_replay(karma_subject):
    """The budget, SHA and agent reward share the caller's rollback boundary."""
    maker, agent_id, project_id = karma_subject
    with pytest.raises(RuntimeError, match="rollback award"):
        async with maker.begin() as db:
            count = await AgentRepository(db).record_commit_shas(agent_id, ["a" * 40], project_id)
            await AgentRepository(db).increment_commits_and_karma(agent_id, count)
            raise RuntimeError("rollback award")
    async with maker.begin() as db:
        repo = AgentRepository(db)
        count = await repo.record_commit_shas(agent_id, ["a" * 40], project_id)
        assert count == 1
        await WebhookRepository(db).increment_commits_and_karma(agent_id, count)
        replay = await repo.record_commit_shas(agent_id, ["a" * 40], project_id)
        assert replay == 0
        await repo.increment_commits_and_karma(agent_id, replay)
        assert await CodeKarmaRepository(db).award(agent_id, 100, 7) == 40
        row = (
            await db.execute(
                text("SELECT karma, code_commits FROM agents WHERE id=:id"), {"id": agent_id}
            )
        ).one()
        assert (row.karma, row.code_commits) == (67, 8)


async def test_agents_have_independent_budgets_across_projects(karma_subject):
    """Project changes cannot bypass one agent's cap or exhaust another's."""
    maker, agent_id, project_id = karma_subject
    other_agent, other_project = uuid4(), uuid4()
    async with maker.begin() as db:
        await db.execute(
            text("INSERT INTO agents (id, name, handle) VALUES (:id, 'Other', 'other')"),
            {"id": other_agent},
        )
        await db.execute(
            text(
                "INSERT INTO projects (id, title, creator_agent_id) "
                "VALUES (:id, 'Other project', :agent)"
            ),
            {"id": other_project, "agent": agent_id},
        )
        service = AgentService(db)
        for index in range(12):
            await service._audit_and_karma(
                {"id": agent_id},
                project_id if index % 2 else other_project,
                "PUT",
                "/contents",
                200,
            )
        assert await CodeKarmaRepository(db).award(other_agent, 10) == 10
        assert (
            await db.scalar(text("SELECT karma FROM agents WHERE id=:id"), {"id": agent_id}) == 67
        )
        assert (
            await db.scalar(
                text("SELECT awarded FROM agent_code_karma_limits WHERE agent_id=:id"),
                {"id": other_agent},
            )
            == 10
        )


@pytest_asyncio.fixture
async def legacy_code_rewards(karma_subject):
    """Commit verifiable pre-V93 rewards in the private migrated test schema."""
    maker, agent_id, project_id = karma_subject
    params = {"id": agent_id, "project": project_id}
    async with maker.begin() as db:
        assert (await db.scalar(text("SELECT current_schema()"))).startswith("a3_")
        await db.execute(text("DROP TABLE agent_code_karma_limits"))
        repo = AgentRepository(db)
        assert await repo.record_commit_shas(agent_id, ["a" * 40], project_id) == 1
        await db.execute(
            text("""
            UPDATE agent_activity
            SET created_at = date_trunc('week', statement_timestamp() AT TIME ZONE 'UTC')
                AT TIME ZONE 'UTC' - interval '1 day'
            WHERE agent_id = :id AND action_type = 'code_commit'
        """),
            params,
        )
        assert (
            await repo.record_commit_shas(
                agent_id, [f"{index:040x}" for index in range(1, 4)], project_id
            )
            == 3
        )
        for index in range(100, 105):
            await GitHubSyncTask._record_sha(db, str(agent_id), str(project_id), f"{index:040x}")
        await db.execute(
            text("""
            INSERT INTO agent_github_actions (agent_id, project_id, method, path, status_code)
            VALUES (:id, :project, 'PUT', '/contents/legacy.py', 200),
                   (:id, :project, 'PUT', '/contents/failed.py', 500)
        """),
            params,
        )
        await db.execute(
            text("UPDATE agents SET karma = 62, code_commits = 4 WHERE id = :id"), params
        )
    return karma_subject


async def test_upgrade_migration_reserves_current_week_legacy_rewards(legacy_code_rewards):
    """Upgrading preserves legacy rewards and reserves only this week's code/file budget."""
    maker, agent_id, project_id = legacy_code_rewards
    params = {"id": agent_id}
    async with maker.begin() as db:
        repo = AgentRepository(db)
        connection = await db.connection()
        raw = await connection.get_raw_connection()
        driver = raw.driver_connection
        assert driver is not None
        migration = Path(__file__).resolve().parents[2] / "db/migrations"
        await driver.execute((migration / "V93__agent_code_karma_limits.sql").read_text())
        assert (
            await db.scalar(
                text("SELECT awarded FROM agent_code_karma_limits WHERE agent_id = :id"), params
            )
            == 35
        )
        row = (
            await db.execute(text("SELECT karma, code_commits FROM agents WHERE id = :id"), params)
        ).one()
        assert (row.karma, row.code_commits) == (62, 4)
        count = await repo.record_commit_shas(
            agent_id, [f"{index:040x}" for index in range(4, 7)], project_id
        )
        assert count == 3
        await repo.increment_commits_and_karma(agent_id, count)
        row = (
            await db.execute(text("SELECT karma, code_commits FROM agents WHERE id = :id"), params)
        ).one()
        assert (row.karma, row.code_commits) == (77, 7)
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM agent_activity "
                    "WHERE agent_id = :id AND action_type = 'code_commit'"
                ),
                params,
            )
            == 12
        )
        assert (
            await db.scalar(
                text("SELECT count(*) FROM agent_github_actions WHERE agent_id = :id"), params
            )
            == 2
        )

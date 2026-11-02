"""Exercise replay accounting through real webhook services and PostgreSQL."""

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.services.agent_service import AgentService
from app.services.github_service import GitHubService
from app.services.webhook_service import GitHubWebhookService, GitLabWebhookService

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
# Relevant production columns from V2/V4/V7/V93/V109; no accounting logic is copied.
SCHEMA = """
CREATE TABLE users (id UUID PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE agents (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), owner_user_id UUID REFERENCES users(id),
 github_user_login TEXT, gitlab_user_login TEXT, is_active BOOLEAN DEFAULT TRUE,
 name TEXT DEFAULT 'fixture', specialization TEXT DEFAULT 'programmer',
 handle TEXT DEFAULT 'fixture', agent_type TEXT DEFAULT 'external',
 code_commits INTEGER DEFAULT 0, karma INTEGER DEFAULT 0
);
CREATE TABLE projects (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), first_pushed_at TIMESTAMPTZ,
 title TEXT DEFAULT 'fixture', repo_url TEXT DEFAULT 'https://github.com/AgentSpore/fixture',
 vcs_provider TEXT DEFAULT 'github', creator_agent_id UUID, team_id UUID,
 merged_prs_count INTEGER DEFAULT 0, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE agent_activity (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), agent_id UUID REFERENCES agents(id),
 project_id UUID REFERENCES projects(id), action_type VARCHAR(50), description TEXT,
 metadata JSONB DEFAULT '{}', created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE project_contributors (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), project_id UUID REFERENCES projects(id),
 agent_id UUID REFERENCES agents(id), owner_user_id UUID REFERENCES users(id),
 contribution_points INTEGER DEFAULT 0, share_pct DECIMAL(5,2) DEFAULT 0,
 tokens_minted BIGINT DEFAULT 0, updated_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(project_id, agent_id)
);
CREATE TABLE funnel_daily (
 day DATE, step TEXT, source TEXT, count BIGINT, PRIMARY KEY(day, step, source)
);
"""


@pytest.fixture(scope="module")
def pg_url():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")


@pytest.fixture
async def accounting(pg_url):
    conn = await asyncpg.connect(pg_url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        await conn.execute(SCHEMA)
        for name in (
            "V37__github_proxy_audit.sql",
            "V81__commit_sha_dedup.sql",
            "V93__agent_code_karma_limits.sql",
        ):
            await conn.execute((MIGRATIONS / name).read_text())
        agent_id = await conn.fetchval(
            "INSERT INTO agents (github_user_login, gitlab_user_login) "
            "VALUES ('synthetic-author', 'synthetic-author') RETURNING id"
        )
        project_id = await conn.fetchval(
            "INSERT INTO projects (creator_agent_id) VALUES ($1) RETURNING id", agent_id
        )
    finally:
        await conn.close()
    engine = create_async_engine(pg_url.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False), agent_id, project_id
    finally:
        await engine.dispose()


def commit(sha, path):
    return {"id": sha, "added": [], "modified": [path], "message": "Edit fixture"}


async def push(session, project_id, commits, provider="github"):
    service = (
        GitHubWebhookService(session) if provider == "github" else GitLabWebhookService(session)
    )
    context = {
        "is_bot": False,
        "project": {"id": project_id, "creator_agent_id": None, "title": "fixture"},
        "sender_login": "synthetic-author",
        "project_data": {},
    }
    await service._on_push({"ref": "refs/heads/main", "commits": commits}, context)
    await session.commit()


@pytest.mark.asyncio
async def test_duplicate_webhook_does_not_award_points_twice(accounting):
    maker, _, project = accounting
    async with maker() as session:
        await push(session, project, [commit("a" * 40, "old")])
        await push(session, project, [commit("a" * 40, "old")])
        points = await session.scalar(text("SELECT contribution_points FROM project_contributors"))
        assert points == 10


@pytest.mark.asyncio
async def test_mixed_batch_awards_only_new_commit_files(accounting):
    maker, _, project = accounting
    async with maker() as session:
        await push(session, project, [commit("a" * 40, "old")])
        await push(session, project, [commit("a" * 40, "old"), commit("b" * 40, "new")])
        points = await session.scalar(text("SELECT contribution_points FROM project_contributors"))
        assert points == 20


@pytest.mark.asyncio
async def test_gitlab_mapped_push_records_real_commit(accounting):
    maker, _, project = accounting
    async with maker() as session:
        await push(session, project, [commit("c" * 40, "new")], "gitlab")
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github", "gitlab"])
async def test_per_commit_paths_are_independent_of_push_batching(accounting, provider):
    maker, _, project = accounting
    async with maker() as session:
        await push(session, project, [commit("a" * 40, "same"), commit("b" * 40, "same")], provider)
        assert (
            await session.scalar(text("SELECT contribution_points FROM project_contributors")) == 20
        )
        await push(session, project, [commit("a" * 40, "same"), commit("b" * 40, "same")], provider)
        assert (
            await session.scalar(text("SELECT contribution_points FROM project_contributors")) == 20
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github", "gitlab"])
async def test_deletion_only_counts_commit_without_file_points(accounting, provider):
    maker, _, project = accounting
    deleted = {"id": "d" * 40, "added": [], "modified": [], "removed": ["gone"]}
    async with maker() as session:
        await push(session, project, [deleted], provider)
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 1
        assert await session.scalar(text("SELECT karma FROM agents")) == 10
        assert await session.scalar(text("SELECT COUNT(*) FROM project_contributors")) == 0


@pytest.mark.asyncio
async def test_concurrent_cross_provider_delivery_awards_once(accounting):
    maker, _, project = accounting

    async def deliver(provider):
        async with maker() as session:
            await push(session, project, [commit("e" * 40, "same")], provider)

    await asyncio.wait_for(asyncio.gather(deliver("github"), deliver("gitlab")), timeout=10)
    async with maker() as session:
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 1
        assert (
            await session.scalar(text("SELECT contribution_points FROM project_contributors")) == 10
        )
        assert await session.scalar(text("SELECT COUNT(*) FROM agent_activity")) == 1


@pytest.mark.asyncio
async def test_failed_points_write_rolls_back_acceptance_and_retry(accounting):
    maker, _, project = accounting
    async with maker() as session:
        await session.execute(
            text("ALTER TABLE project_contributors ADD CHECK(contribution_points < 10)")
        )
        await session.commit()
        with pytest.raises(IntegrityError):
            await push(session, project, [commit("f" * 40, "new")])
        await session.rollback()
        assert await session.scalar(text("SELECT COUNT(*) FROM agent_activity")) == 0
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 0
        await session.execute(
            text(
                "ALTER TABLE project_contributors DROP CONSTRAINT "
                "project_contributors_contribution_points_check"
            )
        )
        await session.commit()
        await push(session, project, [commit("f" * 40, "new")])
        assert (
            await session.scalar(text("SELECT contribution_points FROM project_contributors")) == 10
        )


@pytest.mark.asyncio
async def test_commit_rewards_keep_existing_weekly_karma_cap(accounting):
    maker, _, project = accounting
    async with maker() as session:
        await push(session, project, [commit(f"{n:040x}", "same") for n in range(7)])
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 7
        assert await session.scalar(text("SELECT karma FROM agents")) == 50
        assert (
            await session.scalar(text("SELECT contribution_points FROM project_contributors")) == 70
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy", [False, True])
async def test_actual_github_write_full_sha_then_webhook_replay(accounting, proxy, monkeypatch):
    maker, agent_id, project = accounting
    sha = "9" * 40

    def github_response(request):
        if "/git/ref/heads/" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "8" * 40}})
        if request.method == "GET":
            return httpx.Response(200, json={"tree": {"sha": "7" * 40}})
        if request.method == "PATCH":
            return httpx.Response(200, json={})
        return httpx.Response(201, json={"sha": sha})

    github = GitHubService()
    await github.client.aclose()
    async with httpx.AsyncClient(transport=httpx.MockTransport(github_response)) as client:
        github.client = client
        github._initialized = True
        github._get_installation_token = AsyncMock(return_value="synthetic-fixture")
        github.get_scoped_installation_token = AsyncMock(
            return_value={"token": "synthetic-fixture"}
        )
        async with maker() as session:
            svc = AgentService(session)
            monkeypatch.setattr(
                svc, "git", SimpleNamespace(github=github, _sanitize_repo_name=lambda title: title)
            )
            agent = {"id": agent_id, "name": "fixture", "handle": "synthetic-author"}
            files = [{"path": "same", "content": "fixture"}]
            if proxy:
                result = await svc._proxy_file_write(
                    project,
                    {"title": "fixture"},
                    agent,
                    "PUT",
                    "/contents",
                    {"files": files},
                    "fixture",
                )
                assert result["data"]["sha"] == sha
                monkeypatch.syspath_prepend(str(MIGRATIONS.parents[1] / "sdk"))
                sdk = importlib.import_module("agentspore_sdk.operator_growth_publish")
                assert sdk.GrowthPublisher._commit(result) == sha
                shortened = {**result, "data": {**result["data"], "sha": sha[:7]}}
                assert sdk.GrowthPublisher._commit(shortened) is None
            else:
                result = await svc.push_project_files(project, agent, files, "fixture")
                assert result["sha"] == sha
                await session.commit()
            karma = await session.scalar(text("SELECT karma FROM agents"))
            await push(session, project, [commit(sha, "same")])
            assert (
                await session.scalar(text("SELECT contribution_points FROM project_contributors"))
                == 10
            )
            assert await session.scalar(text("SELECT code_commits FROM agents")) == 1
            assert await session.scalar(text("SELECT karma FROM agents")) == karma

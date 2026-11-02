"""The real sync cycle remains observational for unverified commit authors."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from test_contribution_dedupe import accounting as accounting
from test_contribution_dedupe import commit, push
from test_contribution_dedupe import pg_url as pg_url
from test_contribution_reconciliation import seed_legacy

from app.core import background


async def sync(maker, monkeypatch, commits):
    github = SimpleNamespace(
        initialize=AsyncMock(return_value=True),
        list_pull_requests=AsyncMock(return_value=[]),
        list_commits_page=AsyncMock(return_value=commits),
    )
    monkeypatch.setattr(background, "async_session_maker", maker)
    monkeypatch.setattr(background, "get_github_service", lambda: github)
    monkeypatch.setattr(
        background, "get_openviking_service", lambda: SimpleNamespace(enabled=False)
    )
    await background.GitHubSyncTask().run_once()


@pytest.mark.asyncio
async def test_sync_before_and_after_webhook_cannot_consume_identity(accounting, monkeypatch):
    maker, _, project = accounting
    observed = [{"sha": "a" * 40, "author": "fixture"}]
    await sync(maker, monkeypatch, observed)
    async with maker() as session:
        assert await session.scalar(text("SELECT COUNT(*) FROM agent_activity")) == 0
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 0
        await push(session, project, [commit("a" * 40, "new")])
    await sync(maker, monkeypatch, observed * 4)
    async with maker() as session:
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 1
        assert (
            await session.scalar(text("SELECT contribution_points FROM project_contributors")) == 10
        )


@pytest.mark.asyncio
async def test_sync_preserves_pending_and_gitlab_history(accounting, monkeypatch):
    maker, agent, project = accounting
    async with maker() as session:
        await seed_legacy(session, agent, project)
        await session.execute(text("UPDATE agents SET code_commits=91"))
        gitlab_agent = await session.scalar(
            text("INSERT INTO agents (name, code_commits) VALUES ('gitlab-only', 87) RETURNING id")
        )
        await session.execute(
            text(
                "INSERT INTO projects (vcs_provider, creator_agent_id, repo_url) "
                "VALUES ('gitlab', :agent, 'https://gitlab.com/fixture/other')"
            ),
            {"agent": gitlab_agent},
        )
        await session.commit()
        await push(session, project, [commit("a" * 40, "new")])
    await sync(
        maker,
        monkeypatch,
        [{"sha": "a" * 40, "author": "fixture"}, {"sha": "b" * 40, "author": "gitlab-only"}],
    )
    async with maker() as session:
        assert (
            await session.scalar(
                text("SELECT code_commits FROM agents WHERE id=:id"), {"id": agent}
            )
            == 91
        )
        assert (
            await session.scalar(
                text("SELECT code_commits FROM agents WHERE id=:id"), {"id": gitlab_agent}
            )
            == 87
        )
        assert await session.scalar(text("SELECT COUNT(*) FROM project_contributors")) == 0
        assert (
            await session.scalar(
                text(
                    "SELECT COUNT(*) FROM agent_activity "
                    "WHERE action_type='contribution_unresolved' "
                    "AND metadata->>'state'='pending'"
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                text("SELECT COUNT(*) FROM agent_activity WHERE action_type='code_commit'")
            )
            == 1
        )

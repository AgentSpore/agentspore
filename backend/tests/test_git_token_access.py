"""Tests for git-token access control.

Verifies that only the project creator or a team member can obtain
a push token. Other agents receive 403 with a message suggesting
fork + pull request.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport

AGENT_CREATOR_ID = str(uuid.uuid4())
AGENT_OUTSIDER_ID = str(uuid.uuid4())
AGENT_TEAMMATE_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
TEAM_ID = str(uuid.uuid4())


def _make_agent(agent_id: str, name: str = "TestAgent") -> dict:
    return {
        "id": agent_id,
        "name": name,
        "specialization": "programmer",
        "is_active": True,
        "github_token_encrypted": None,
        "github_token_expires_at": None,
    }


def _make_project(creator_id: str, team_id: str | None = None) -> dict:
    return {
        "title": "TestProject",
        "repo_url": "https://github.com/AgentSpore/testproject",
        "vcs_provider": "github",
        "creator_agent_id": creator_id,
        "team_id": team_id,
    }


@pytest.fixture
def app_with_agent():
    """FastAPI app with get_agent_by_api_key and get_agent_service overridden."""
    from app.main import app
    from app.core.database import get_db
    from app.core.redis_client import get_redis
    from app.services.agent_service import get_agent_by_api_key, get_agent_service

    mock_db = AsyncMock()
    _current_agent = {}
    _svc_mock = MagicMock()

    async def override_get_db():
        yield mock_db

    async def override_get_agent():
        return _current_agent["agent"]

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: AsyncMock()
    app.dependency_overrides[get_agent_by_api_key] = override_get_agent
    app.dependency_overrides[get_agent_service] = lambda: _svc_mock

    yield app, mock_db, _current_agent, _svc_mock

    app.dependency_overrides.clear()


@pytest.mark.asyncio
class TestGitTokenAccessControl:

    async def test_creator_gets_token(self, app_with_agent):
        """Project creator should receive a git token."""
        app, mock_db, agent_ref, svc_mock = app_with_agent
        agent_ref["agent"] = _make_agent(AGENT_CREATOR_ID, "Creator")

        svc_mock.get_project_git_token = AsyncMock(return_value={
            "token": "ghp_fake",
            "repo_url": "https://github.com/AgentSpore/testproject",
            "provider": "github",
        })

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(
                f"/api/v1/agents/projects/{PROJECT_ID}/git-token",
                headers={"X-API-Key": "any"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["repo_url"] == "https://github.com/AgentSpore/testproject"

    async def test_outsider_gets_403(self, app_with_agent):
        """Agent that is neither creator nor team member gets 403."""
        from fastapi import HTTPException
        app, mock_db, agent_ref, svc_mock = app_with_agent
        agent_ref["agent"] = _make_agent(AGENT_OUTSIDER_ID, "Outsider")

        svc_mock.get_project_git_token = AsyncMock(
            side_effect=HTTPException(
                status_code=403,
                detail="Access denied: only the project creator or a team member can push. Use fork + pull request instead.",
            )
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(
                f"/api/v1/agents/projects/{PROJECT_ID}/git-token",
                headers={"X-API-Key": "any"},
            )

        assert resp.status_code == 403
        assert "fork" in resp.json()["detail"].lower()

    async def test_outsider_with_team_but_not_member_gets_403(self, app_with_agent):
        """Agent not in the project's team gets 403 even if team exists."""
        from fastapi import HTTPException
        app, mock_db, agent_ref, svc_mock = app_with_agent
        agent_ref["agent"] = _make_agent(AGENT_OUTSIDER_ID, "Outsider")

        svc_mock.get_project_git_token = AsyncMock(
            side_effect=HTTPException(
                status_code=403,
                detail="Access denied: only the project creator or a team member can push. Use fork + pull request instead.",
            )
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(
                f"/api/v1/agents/projects/{PROJECT_ID}/git-token",
                headers={"X-API-Key": "any"},
            )

        assert resp.status_code == 403

    async def test_team_member_gets_token(self, app_with_agent):
        """Team member (not creator) should receive a git token."""
        app, mock_db, agent_ref, svc_mock = app_with_agent
        agent_ref["agent"] = _make_agent(AGENT_TEAMMATE_ID, "Teammate")

        svc_mock.get_project_git_token = AsyncMock(return_value={
            "token": "ghp_team",
            "repo_url": "https://github.com/AgentSpore/testproject",
            "provider": "github",
        })

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(
                f"/api/v1/agents/projects/{PROJECT_ID}/git-token",
                headers={"X-API-Key": "any"},
            )

        assert resp.status_code == 200
        assert "token" in resp.json()

    async def test_nonexistent_project_returns_404(self, app_with_agent):
        """Non-existent project should return 404."""
        from fastapi import HTTPException
        app, mock_db, agent_ref, svc_mock = app_with_agent
        agent_ref["agent"] = _make_agent(AGENT_CREATOR_ID)

        svc_mock.get_project_git_token = AsyncMock(
            side_effect=HTTPException(status_code=404, detail="Project not found")
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(
                f"/api/v1/agents/projects/{PROJECT_ID}/git-token",
                headers={"X-API-Key": "any"},
            )

        assert resp.status_code == 404

    async def test_403_message_suggests_pr(self, app_with_agent):
        """403 response should suggest using fork + pull request."""
        from fastapi import HTTPException
        app, mock_db, agent_ref, svc_mock = app_with_agent
        agent_ref["agent"] = _make_agent(AGENT_OUTSIDER_ID)

        svc_mock.get_project_git_token = AsyncMock(
            side_effect=HTTPException(
                status_code=403,
                detail="Access denied: only the project creator or a team member can push. Use fork + pull request instead.",
            )
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(
                f"/api/v1/agents/projects/{PROJECT_ID}/git-token",
                headers={"X-API-Key": "any"},
            )

        detail = resp.json()["detail"]
        assert "pull request" in detail.lower()
        assert "creator" in detail.lower() or "team" in detail.lower()

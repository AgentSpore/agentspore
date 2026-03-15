"""Tests for git-token access control.

Verifies that only the project creator or a team member can obtain
a push token. Other agents receive 403 with a message suggesting
fork + pull request.
"""

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

# Mock /app/logs creation before importing app.main (path doesn't exist outside Docker)
_orig_mkdir = Path.mkdir


def _patched_mkdir(self, *args, **kwargs):
    if str(self).startswith("/app"):
        return None
    return _orig_mkdir(self, *args, **kwargs)


patch.object(Path, "mkdir", _patched_mkdir).start()
# Mock RotatingFileHandler to avoid writing to /app/logs/app.log
patch("logging.handlers.RotatingFileHandler", return_value=MagicMock()).start()

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
    """FastAPI app with get_agent_by_api_key overridden via dependency override."""
    from app.main import app
    from app.core.database import get_db
    from app.services.agent_service import get_agent_by_api_key

    mock_db = AsyncMock()
    _current_agent = {}

    async def override_get_db():
        yield mock_db

    async def override_get_agent():
        return _current_agent["agent"]

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_agent_by_api_key] = override_get_agent

    yield app, mock_db, _current_agent

    app.dependency_overrides.clear()


@pytest.mark.asyncio
class TestGitTokenAccessControl:

    async def test_creator_gets_token(self, app_with_agent):
        """Project creator should receive a git token."""
        app, mock_db, agent_ref = app_with_agent
        agent_ref["agent"] = _make_agent(AGENT_CREATOR_ID, "Creator")

        with (
            patch("app.repositories.agent_repo.get_project_basic", new_callable=AsyncMock) as mock_proj,
            patch("app.api.v1.agents._svc") as mock_svc,
        ):
            mock_proj.return_value = _make_project(AGENT_CREATOR_ID)
            mock_svc.return_value.ensure_github_token = AsyncMock(return_value="ghp_fake")

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
        app, mock_db, agent_ref = app_with_agent
        agent_ref["agent"] = _make_agent(AGENT_OUTSIDER_ID, "Outsider")

        with patch("app.repositories.agent_repo.get_project_basic", new_callable=AsyncMock) as mock_proj:
            mock_proj.return_value = _make_project(AGENT_CREATOR_ID)  # no team

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get(
                    f"/api/v1/agents/projects/{PROJECT_ID}/git-token",
                    headers={"X-API-Key": "any"},
                )

        assert resp.status_code == 403
        assert "fork" in resp.json()["detail"].lower()

    async def test_outsider_with_team_but_not_member_gets_403(self, app_with_agent):
        """Agent not in the project's team gets 403 even if team exists."""
        app, mock_db, agent_ref = app_with_agent
        agent_ref["agent"] = _make_agent(AGENT_OUTSIDER_ID, "Outsider")

        with (
            patch("app.repositories.agent_repo.get_project_basic", new_callable=AsyncMock) as mock_proj,
            patch("app.repositories.hackathon_repo.is_team_member", new_callable=AsyncMock) as mock_member,
        ):
            mock_proj.return_value = _make_project(AGENT_CREATOR_ID, team_id=TEAM_ID)
            mock_member.return_value = False

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get(
                    f"/api/v1/agents/projects/{PROJECT_ID}/git-token",
                    headers={"X-API-Key": "any"},
                )

        assert resp.status_code == 403
        mock_member.assert_awaited_once()

    async def test_team_member_gets_token(self, app_with_agent):
        """Team member (not creator) should receive a git token."""
        app, mock_db, agent_ref = app_with_agent
        agent_ref["agent"] = _make_agent(AGENT_TEAMMATE_ID, "Teammate")

        with (
            patch("app.repositories.agent_repo.get_project_basic", new_callable=AsyncMock) as mock_proj,
            patch("app.repositories.hackathon_repo.is_team_member", new_callable=AsyncMock) as mock_member,
            patch("app.api.v1.agents._svc") as mock_svc,
        ):
            mock_proj.return_value = _make_project(AGENT_CREATOR_ID, team_id=TEAM_ID)
            mock_member.return_value = True
            mock_svc.return_value.ensure_github_token = AsyncMock(return_value="ghp_team")

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get(
                    f"/api/v1/agents/projects/{PROJECT_ID}/git-token",
                    headers={"X-API-Key": "any"},
                )

        assert resp.status_code == 200
        assert "token" in resp.json()
        mock_member.assert_awaited_once_with(mock_db, TEAM_ID, AGENT_TEAMMATE_ID)

    async def test_nonexistent_project_returns_404(self, app_with_agent):
        """Non-existent project should return 404."""
        app, mock_db, agent_ref = app_with_agent
        agent_ref["agent"] = _make_agent(AGENT_CREATOR_ID)

        with patch("app.repositories.agent_repo.get_project_basic", new_callable=AsyncMock) as mock_proj:
            mock_proj.return_value = None

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get(
                    f"/api/v1/agents/projects/{PROJECT_ID}/git-token",
                    headers={"X-API-Key": "any"},
                )

        assert resp.status_code == 404

    async def test_403_message_suggests_pr(self, app_with_agent):
        """403 response should suggest using fork + pull request."""
        app, mock_db, agent_ref = app_with_agent
        agent_ref["agent"] = _make_agent(AGENT_OUTSIDER_ID)

        with patch("app.repositories.agent_repo.get_project_basic", new_callable=AsyncMock) as mock_proj:
            mock_proj.return_value = _make_project(AGENT_CREATOR_ID)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get(
                    f"/api/v1/agents/projects/{PROJECT_ID}/git-token",
                    headers={"X-API-Key": "any"},
                )

        detail = resp.json()["detail"]
        assert "pull request" in detail.lower()
        assert "creator" in detail.lower() or "team" in detail.lower()

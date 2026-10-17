"""Project creation reports an unavailable VCS through the existing `warning` field.

Only the outbound GitHub boundary is replaced; the API, auth and PostgreSQL are real.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_service_usage import usage_api as usage_api
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world

from app.services import agent_service
from app.services.agent_service import AgentService
from app.services.github_service import GitHubService
from app.services.openviking_service import OpenVikingService

pytestmark = pytest.mark.integration
PATH = "/api/v1/agents/projects"
PAYLOAD = {"title": "VCS warning project", "description": "Warning acceptance"}
REPO_URL = "https://github.com/example-org/vcs-warning-project"
SKILL_MD = Path(__file__).resolve().parents[2] / "skill.md"


async def test_missing_repository_is_reported_in_warning(usage_api, usage_world, monkeypatch):
    monkeypatch.setattr(GitHubService, "create_repo", AsyncMock(return_value=None))
    response = await usage_api.post(PATH, json=PAYLOAD, headers={"X-API-Key": usage_world.keys[1]})
    assert response.status_code == 200
    body = response.json()
    assert body["repo_url"] is None
    assert body["warning"].startswith("vcs_unavailable:")
    assert "push" in body["warning"]
    assert "lacks access to the organization" not in body["warning"]


async def test_oauth_repository_failure_mentions_account_access(
    usage_api, usage_world, monkeypatch
):
    monkeypatch.setattr(GitHubService, "create_repo", AsyncMock(return_value=None))
    monkeypatch.setattr(
        AgentService, "ensure_github_token", AsyncMock(return_value="oauth-placeholder")
    )
    response = await usage_api.post(PATH, json=PAYLOAD, headers={"X-API-Key": usage_world.keys[1]})
    assert response.status_code == 200
    warning = response.json()["warning"]
    assert warning.startswith("vcs_unavailable:")
    assert "lacks access to the organization" in warning


async def test_created_repository_leaves_warning_empty(usage_api, usage_world, monkeypatch):
    monkeypatch.setattr(GitHubService, "create_repo", AsyncMock(return_value=REPO_URL))
    monkeypatch.setattr(GitHubService, "setup_repo_admin", AsyncMock(return_value=None))
    monkeypatch.setattr(GitHubService, "push_files", AsyncMock(return_value=True))
    response = await usage_api.post(PATH, json=PAYLOAD, headers={"X-API-Key": usage_world.keys[1]})
    assert response.status_code == 200
    body = response.json()
    assert body["repo_url"] == REPO_URL
    assert body["warning"] is None


def test_skill_md_documents_null_repo_url():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "vcs_unavailable" in text


async def test_vcs_and_similar_warnings_are_combined(usage_api, usage_world, monkeypatch):
    monkeypatch.setattr(GitHubService, "create_repo", AsyncMock(return_value=None))
    ov = OpenVikingService()
    ov.enabled = True
    monkeypatch.setattr(
        ov, "find_similar_projects", AsyncMock(return_value=[{"content": "Existing twin"}])
    )
    monkeypatch.setattr(ov, "index_project", AsyncMock(return_value=None))
    monkeypatch.setattr(ov, "link_resources", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_service, "get_openviking_service", lambda: ov)
    response = await usage_api.post(PATH, json=PAYLOAD, headers={"X-API-Key": usage_world.keys[1]})
    assert response.status_code == 200
    vcs_part, _, similar_part = response.json()["warning"].partition("; ")
    assert vcs_part.startswith("vcs_unavailable:")
    assert "Similar projects already exist: Existing twin" in similar_part

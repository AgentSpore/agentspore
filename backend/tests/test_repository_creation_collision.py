"""Reject repository collisions through real provider, service, API and PG paths."""

import json
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
import test_service_usage
from sqlalchemy import text

from app.services import agent_service
from app.services.agent_service import AgentService
from app.services.git_service import GitService
from app.services.github_service import GitHubService

usage_api = test_service_usage.usage_api
usage_pg = test_service_usage.usage_pg
usage_world = test_service_usage.usage_world

COLLISION = {
    "message": "Validation Failed",
    "errors": [
        {
            "resource": "Repository",
            "field": "name",
            "code": "custom",
            "message": "name already exists on this account",
        },
    ],
}


@pytest.fixture
async def provider(monkeypatch):
    service = GitHubService()
    await service.client.aclose()
    service.base_url = "https://api.github.test"
    service.org = "synthetic-org"
    service._initialized = True
    service._installation_token = "synthetic-installation"
    service._token_expires_at = float("inf")
    calls = []
    behavior: dict[str, Any] = {"status": 422, "body": COLLISION, "timeout": False}

    def respond(request):
        calls.append(request)
        if behavior["timeout"]:
            raise httpx.ReadTimeout("synthetic unknown create outcome", request=request)
        return httpx.Response(behavior["status"], json=behavior["body"])

    service.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    git = GitService()
    git._github = service
    monkeypatch.setattr(agent_service, "get_git_service", lambda: git)
    try:
        yield service, calls, behavior
    finally:
        await service.client.aclose()


@pytest.mark.parametrize("personal", [False, True])
@pytest.mark.parametrize("title", ["existing-repo", "Existing Repo!!"])
async def test_existing_repository_is_not_a_creation_receipt(provider, personal, title):
    service, calls, _ = provider
    if personal:
        result = await service.create_user_repo(
            title, user_token="synthetic-oauth", user_login="synthetic-user"
        )
    else:
        result = await service.create_repo(title, user_token="synthetic-oauth")
    assert result is None
    assert len(calls) == 1
    assert json.loads(calls[0].content)["name"] == "existing-repo"


@pytest.mark.parametrize("personal", [False, True])
async def test_successful_creation_still_returns_new_repository(provider, personal):
    service, calls, behavior = provider
    behavior.update(status=201, body={"html_url": "https://github.com/synthetic-user/new-repo"})
    if personal:
        result = await service.create_user_repo(
            "New Repo", user_token="synthetic-oauth", user_login="synthetic-user"
        )
        assert result == "https://github.com/synthetic-user/new-repo"
    else:
        result = await service.create_repo("New Repo")
        assert result == "https://github.com/synthetic-org/new-repo"
    assert len(calls) == 1


@pytest.mark.parametrize("personal", [False, True])
async def test_unknown_create_outcome_never_replays_or_binds(provider, personal):
    service, calls, behavior = provider
    behavior["timeout"] = True
    if personal:
        result = await service.create_user_repo(
            "New Repo", user_token="synthetic-oauth", user_login="synthetic-user"
        )
    else:
        result = await service.create_repo("New Repo")
    assert result is None
    assert len(calls) == 1


@pytest.mark.integration
@pytest.mark.parametrize("oauth", [False, True])
@pytest.mark.parametrize("title", ["existing-repo", "Existing Repo!!"])
async def test_collision_cannot_bind_or_mutate_foreign_repository(
    usage_api,
    usage_world,
    provider,
    monkeypatch,
    oauth,
    title,
):
    service, calls, _ = provider
    monkeypatch.setattr(
        AgentService,
        "ensure_github_token",
        AsyncMock(return_value="synthetic-oauth" if oauth else None),
    )
    foreign_url = "https://github.com/synthetic-org/existing-repo"
    async with usage_world.maker.begin() as db:
        await db.execute(
            text("UPDATE projects SET repo_url=:url WHERE id=:id"),
            {"url": foreign_url, "id": usage_world.projects[0]},
        )
        await db.execute(
            text("UPDATE agents SET github_user_login='synthetic-user' WHERE id=:id"),
            {"id": usage_world.agents[1]},
        )
    headers = {"X-API-Key": usage_world.keys[1]}
    response = await usage_api.post(
        "/api/v1/agents/projects", json={"title": title}, headers=headers
    )
    assert response.status_code == 200
    # Only create was attempted: no protection, collaborators, README or token request.
    assert [(r.method, r.url.path) for r in calls] == [("POST", "/orgs/synthetic-org/repos")]
    body = response.json()
    assert body["repo_url"] is None
    assert body["warning"].startswith("vcs_unavailable:")
    async with usage_world.maker() as db:
        row = (
            await db.execute(
                text("SELECT repo_url, creator_agent_id FROM projects WHERE id=:id"),
                {"id": UUID(body["id"])},
            )
        ).one()
        assert row.repo_url is None
        assert row.creator_agent_id == usage_world.agents[1]
        owners = (
            (
                await db.execute(
                    text("SELECT creator_agent_id FROM projects WHERE repo_url=:url"),
                    {"url": foreign_url},
                )
            )
            .scalars()
            .all()
        )
        assert owners == [usage_world.agents[0]]
    token = await usage_api.get(f"/api/v1/agents/projects/{body['id']}/git-token", headers=headers)
    assert token.status_code == 400
    proxy = await usage_api.post(
        f"/api/v1/agents/projects/{body['id']}/github",
        json={
            "method": "PUT",
            "path": "/contents",
            "body": {
                "files": [{"path": "README.md", "content": "must not overwrite"}],
                "message": "synthetic",
                "branch": "main",
            },
        },
        headers=headers,
    )
    assert proxy.status_code == 400
    legacy = await usage_api.post(
        f"/api/v1/agents/projects/{body['id']}/push",
        json={
            "files": [{"path": "README.md", "content": "must not overwrite"}],
            "commit_message": "synthetic",
        },
        headers=headers,
    )
    assert legacy.status_code == 400
    base = f"/api/v1/agents/projects/{body['id']}"
    merge = await usage_api.post(base + "/merge-pr", json={"pr_number": 1}, headers=headers)
    assert merge.status_code == 400
    comment = await usage_api.post(
        base + "/issues/1/comments", json={"body": "synthetic"}, headers=headers
    )
    assert comment.status_code == 400
    for suffix in (
        "/issues",
        "/issues/1/comments",
        "/pull-requests",
        "/pull-requests/1/comments",
        "/pull-requests/1/review-comments",
        "/commits",
        "/files/private.txt",
    ):
        denied = await usage_api.get(base + suffix, headers=headers)
        assert denied.status_code == 400, suffix
    assert (await usage_api.get(base + "/files", headers=headers)).json() == []
    for path, field in (("my-issues", "issues"), ("my-prs", "pull_requests")):
        aggregate = await usage_api.get("/api/v1/agents/" + path, headers=headers)
        assert aggregate.status_code == 200
        assert aggregate.json()[field] == []
        assert aggregate.json()["projects_checked"] == 0
    assert len(calls) == 1


@pytest.mark.integration
async def test_bound_project_keeps_token_and_discovery_paths(
    usage_api, usage_world, provider, monkeypatch
):
    _, calls, behavior = provider
    behavior.update(status=200, body=[])
    project = usage_world.projects[2]
    url = "https://github.com/synthetic-org/bound-repo"
    async with usage_world.maker.begin() as db:
        await db.execute(
            text("UPDATE projects SET title='bound-repo', repo_url=:url WHERE id=:id"),
            {"url": url, "id": project},
        )
        await db.execute(
            text("UPDATE projects SET repo_url='', creator_agent_id=:agent WHERE id=:id"),
            {"id": usage_world.projects[1], "agent": usage_world.agents[1]},
        )
    monkeypatch.setattr(
        AgentService, "ensure_github_token", AsyncMock(return_value="synthetic-oauth")
    )
    headers = {"X-API-Key": usage_world.keys[1]}
    token = await usage_api.get(f"/api/v1/agents/projects/{project}/git-token", headers=headers)
    assert token.status_code == 200
    assert token.json()["repo_url"] == url
    for path in (f"projects/{project}/issues", "my-issues", "my-prs"):
        response = await usage_api.get("/api/v1/agents/" + path, headers=headers)
        assert response.status_code == 200
        if path.startswith("my-"):
            assert response.json()["projects_checked"] == 1
    assert calls and all("/bound-repo/" in request.url.path for request in calls)

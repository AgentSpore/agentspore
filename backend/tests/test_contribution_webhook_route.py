"""Exercise the signed GitLab route with actual migrated accounting tables."""

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from test_agent_code_karma_limits import karma_subject as karma_subject
from test_agent_registration_limits import a3_db as a3_db
from test_agent_registration_limits import a3_pg as a3_pg

from app.api.v1 import webhooks
from app.core.database import get_db


@pytest.mark.asyncio
async def test_gitlab_route_auth_and_replay_on_migrated_database(karma_subject, monkeypatch):
    maker, agent, project = karma_subject
    async with maker.begin() as db:
        await db.execute(
            text("UPDATE agents SET gitlab_user_login='a3-contributor' WHERE id=:id"), {"id": agent}
        )
        await db.execute(
            text("UPDATE projects SET vcs_provider='gitlab' WHERE id=:id"), {"id": project}
        )
    app = FastAPI()
    app.include_router(webhooks.router, prefix="/api/v1")

    async def request_db():
        async with maker.begin() as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    monkeypatch.setattr(webhooks, "GITLAB_WEBHOOK_SECRET", "synthetic-route-fixture")
    body = {
        "project": {"path_with_namespace": "org/a3-project"},
        "user": {"username": "a3-contributor"},
        "ref": "refs/heads/main",
        "commits": [{"id": "a" * 40, "added": [], "modified": ["app.py"], "removed": []}],
    }
    headers = {"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "synthetic-route-fixture"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        denied = await client.post("/api/v1/webhooks/gitlab", json=body)
        assert denied.status_code == 401
        for _ in range(2):
            response = await client.post("/api/v1/webhooks/gitlab", json=body, headers=headers)
            assert response.status_code == 200
            assert response.json()["type"] == "agent_push"
    async with maker() as db:
        assert (
            await db.scalar(text("SELECT code_commits FROM agents WHERE id=:id"), {"id": agent})
            == 1
        )
        assert (
            await db.scalar(
                text("SELECT contribution_points FROM project_contributors WHERE agent_id=:id"),
                {"id": agent},
            )
            == 10
        )
        assert await db.scalar(text("SELECT karma FROM agents WHERE id=:id"), {"id": agent}) == 27

"""Project metrics opt-in through real PostgreSQL, authentication and ASGI.

Run under the coordinator's synthetic environment with external services disabled.
"""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_service_usage import usage_api as usage_api
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world

from app.services.github_service import GitHubService

pytestmark = pytest.mark.integration
PATH = "/api/v1/agents/projects"
PAYLOAD = {"title": "D1 synthetic project", "description": "Metrics profile acceptance"}


@pytest.fixture(autouse=True)
def github_boundary(monkeypatch):
    """Prevent repository creation while exercising the actual project producer."""
    monkeypatch.setattr(GitHubService, "create_repo", AsyncMock(return_value=None))


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        pytest.param({}, None, id="legacy-omitted"),
        pytest.param({"metrics_profile": None}, None, id="explicit-null"),
        pytest.param({"metrics_profile": "family_safety"}, "family_safety", id="opt-in"),
    ],
)
async def test_creator_profile_is_returned_and_persisted(usage_api, usage_world, extra, expected):
    response = await usage_api.post(
        PATH, json={**PAYLOAD, **extra}, headers={"X-API-Key": usage_world.keys[1]}
    )
    assert response.status_code == 200
    result = response.json()
    assert "metrics_profile" in result
    assert result["metrics_profile"] == expected
    assert result["creator_agent_id"] == str(usage_world.agents[1])
    async with usage_world.maker() as db:
        row = (
            await db.execute(
                text(
                    "SELECT creator_agent_id, to_jsonb(p) ? 'metrics_profile' AS has_profile, "
                    "to_jsonb(p)->>'metrics_profile' AS profile FROM projects p WHERE id=:id"
                ),
                {"id": UUID(result["id"])},
            )
        ).one()
    assert row.creator_agent_id == usage_world.agents[1]
    assert row.has_profile
    assert row.profile == expected


async def test_unknown_profile_is_rejected_without_creating_project(usage_api, usage_world):
    async with usage_world.maker() as db:
        before = await db.scalar(text("SELECT count(*) FROM projects"))
    response = await usage_api.post(
        PATH,
        json={**PAYLOAD, "metrics_profile": "unregistered"},
        headers={"X-API-Key": usage_world.keys[1]},
    )
    assert response.status_code == 422
    assert any(error["loc"] == ["body", "metrics_profile"] for error in response.json()["detail"])
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM projects")) == before


async def test_invalid_key_cannot_declare_profile_or_create_project(usage_api, usage_world):
    async with usage_world.maker() as db:
        before = await db.scalar(text("SELECT count(*) FROM projects"))
    response = await usage_api.post(
        PATH,
        json={**PAYLOAD, "metrics_profile": "family_safety"},
        headers={"X-API-Key": str(uuid4())},
    )
    assert response.status_code == 401
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM projects")) == before

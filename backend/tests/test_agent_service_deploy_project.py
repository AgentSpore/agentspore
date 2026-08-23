"""Tests for AgentService.deploy_project / POST /projects/:id/deploy.

Service-level tests mock repo (no real DB or runner needed). The route-level
test exercises the full router -> service path via ASGITransport, catching
the KeyError an agent would hit reading resp["status"] on a dict-detail
HTTPException (FastAPI wraps it as {"detail": {...}}).
"""

from __future__ import annotations

import uuid
from typing import NoReturn, Protocol
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from httpx import ASGITransport

from app.main import app as fastapi_app
from app.services.agent_service import AgentService, get_agent_by_api_key, get_agent_service


class _MockedSvc(Protocol):
    """Structural type for AgentService with mock collaborators."""

    repo: AsyncMock

    async def deploy_project(self, project_id: UUID, agent: dict) -> NoReturn: ...


@pytest.fixture()
def agent_svc() -> _MockedSvc:
    """AgentService with repo mocked; no __init__ side-effects."""
    svc = AgentService.__new__(AgentService)
    svc.repo = AsyncMock()
    svc.redis = None
    return svc  # type: ignore[return-value]


AGENT = {"id": str(uuid4()), "handle": "test-agent"}


@pytest.mark.asyncio
async def test_deploy_project_not_found_raises_404(agent_svc: _MockedSvc) -> None:
    agent_svc.repo.get_project_basic = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await agent_svc.deploy_project(uuid4(), AGENT)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_deploy_project_reports_unavailable_not_deployed(agent_svc: _MockedSvc) -> None:
    """No real deploy backend exists — the call must not claim success.

    Regression guard: previously this wrote a fabricated
    https://preview.agentspore.com/{id} URL to the DB and returned
    status="deployed". That must no longer happen.
    """
    agent_svc.repo.get_project_basic = AsyncMock(
        return_value={"id": str(uuid4()), "title": "t", "repo_url": None}
    )

    with pytest.raises(HTTPException) as exc_info:
        await agent_svc.deploy_project(uuid4(), AGENT)

    assert exc_info.value.status_code == 501
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["status"] != "deployed"
    assert "preview.agentspore.com" not in str(detail)

    # only the existence check ran — nothing else on repo was called, so no
    # write of any kind (fabricated or otherwise) reached the DB.
    agent_svc.repo.get_project_basic.assert_awaited_once()
    assert agent_svc.repo.method_calls == [
        c for c in agent_svc.repo.method_calls if c[0] == "get_project_basic"
    ]


# ── Route-level test: full router -> service path via ASGITransport ─────────
#
# Regression guard for the "agent reads resp['status'] at the top level"
# failure mode: FastAPI wraps a dict-detail HTTPException as
# {"detail": {...}}, so "status" only exists nested under "detail". A test
# that only calls the service method (above) cannot see this — it inspects
# exc_info.value.detail directly, bypassing FastAPI's response envelope.


@pytest.fixture
def deploy_route_client():
    """Yield an AsyncClient with agent auth + AgentService overridden."""
    mock_agent = {"id": str(uuid.uuid4()), "handle": "test-agent"}
    svc_mock = AsyncMock(spec=AgentService)
    svc_mock.deploy_project.side_effect = HTTPException(
        status_code=501,
        detail={"status": "not_implemented", "message": "no deploy backend"},
    )

    fastapi_app.dependency_overrides[get_agent_by_api_key] = lambda: mock_agent
    fastapi_app.dependency_overrides[get_agent_service] = lambda: svc_mock

    transport = ASGITransport(app=fastapi_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    yield client

    fastapi_app.dependency_overrides.pop(get_agent_by_api_key, None)
    fastapi_app.dependency_overrides.pop(get_agent_service, None)


@pytest.mark.asyncio
async def test_deploy_route_returns_501_with_nested_status(deploy_route_client) -> None:
    async with deploy_route_client as client:
        resp = await client.post(f"/api/v1/agents/projects/{uuid.uuid4()}/deploy")

    assert resp.status_code == 501
    body = resp.json()
    assert "status" not in body
    assert body["detail"]["status"] == "not_implemented"

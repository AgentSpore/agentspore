"""Unit tests for AgentService.deploy_project — no real deploy backend exists.

All external collaborators (repo) are mocked. No real DB or runner needed.
"""

from __future__ import annotations

from typing import Protocol
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.services.agent_service import AgentService


class _MockedSvc(Protocol):
    """Structural type for AgentService with mock collaborators."""

    repo: AsyncMock

    async def deploy_project(self, project_id: UUID, agent: dict) -> dict: ...


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

    agent_svc.repo.update_project_deployed.assert_not_called()

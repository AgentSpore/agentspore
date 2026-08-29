"""Tests for AgentService.list_projects mine=true identity guard.

Measured live 2026-08-29: GET /api/v1/agents/projects?mine=true with no
X-API-Key (or an unrecognised one) silently fell through to the unfiltered
"1=1" query and returned ALL platform projects instead of failing. A scout
agent's dedup step compared its new idea against every other agent's projects,
found a false overlap, and skipped legitimate project creation.

Service-level tests mock repo (no real DB needed) — what's under test is the
guard/branch logic in list_projects itself, which runs for real; only the
final DB read (list_agent_projects) is mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.agent_service import AgentService


@pytest.fixture()
def agent_svc() -> AgentService:
    svc = AgentService.__new__(AgentService)
    svc.repo = AsyncMock()
    svc.repo.list_agent_projects = AsyncMock(return_value=[])
    svc.redis = None
    return svc  # type: ignore[return-value]


def _kwargs(**overrides) -> dict:
    base = dict(
        limit=100,
        needs_review=None,
        has_open_issues=None,
        category=None,
        status=None,
        tech_stack=None,
        mine=True,
        x_api_key=None,
    )
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_mine_true_no_api_key_raises_401(agent_svc: AgentService) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await agent_svc.list_projects(**_kwargs(x_api_key=None))

    assert exc_info.value.status_code == 401
    agent_svc.repo.list_agent_projects.assert_not_called()


@pytest.mark.asyncio
async def test_mine_true_unknown_api_key_raises_401(agent_svc: AgentService) -> None:
    agent_svc.repo.get_agent_id_by_api_key_hash = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await agent_svc.list_projects(**_kwargs(x_api_key="unknown-key"))

    assert exc_info.value.status_code == 401
    agent_svc.repo.list_agent_projects.assert_not_called()


@pytest.mark.asyncio
async def test_mine_true_valid_key_filters_by_creator_agent_id(agent_svc: AgentService) -> None:
    agent_id = uuid4()
    agent_svc.repo.get_agent_id_by_api_key_hash = AsyncMock(return_value=agent_id)

    await agent_svc.list_projects(**_kwargs(x_api_key="valid-key"))

    agent_svc.repo.list_agent_projects.assert_awaited_once()
    where_clause, params = agent_svc.repo.list_agent_projects.call_args.args
    assert "p.creator_agent_id = :mine_agent_id" in where_clause
    assert params["mine_agent_id"] == agent_id


@pytest.mark.asyncio
async def test_mine_unset_returns_unfiltered_public_list(agent_svc: AgentService) -> None:
    await agent_svc.list_projects(**_kwargs(mine=None, x_api_key=None))

    agent_svc.repo.list_agent_projects.assert_awaited_once()
    where_clause, params = agent_svc.repo.list_agent_projects.call_args.args
    assert "creator_agent_id" not in where_clause
    assert "mine_agent_id" not in params

"""Unit tests for HostedAgentService — quota enforcement and create flow.

No real DB or runner required; all external deps are mocked.
"""

from __future__ import annotations

from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_create_svc(existing_agent_count: int = 0, max_hosted: int = 1):
    """Build a HostedAgentService suitable for testing create_hosted_agent."""
    from app.services.hosted_agent_service import HostedAgentService

    repo = AsyncMock()
    repo.count_by_owner = AsyncMock(return_value=existing_agent_count)
    repo.create = AsyncMock(return_value={
        "id": "new-hosted-id",
        "agent_id": "new-agent-id",
        "owner_user_id": "u1",
        "status": "stopped",
        "model": "test/model:free",
        "system_prompt": "hello",
        "runtime": "python-minimal",
        "memory_limit_mb": 256,
    })
    repo.upsert_file = AsyncMock(return_value={"file_path": "AGENT.md", "version": 1})
    repo.db = AsyncMock()

    agent_svc = AsyncMock()
    agent_svc.db = AsyncMock()
    agent_svc.register_agent = AsyncMock(return_value={
        "agent_id": "new-agent-id",
        "api_key": "test-api-key",
        "handle": "test-handle",
    })

    openrouter = AsyncMock()
    openrouter.is_allowed = AsyncMock(return_value=True)

    openviking = AsyncMock()
    openviking.enabled = False

    settings = MagicMock()
    settings.max_hosted_agents_per_user = max_hosted
    settings.agent_runner_url = ""
    settings.agent_runner_key = ""
    settings.oauth_redirect_base_url = "https://agentspore.com"

    svc = HostedAgentService.__new__(HostedAgentService)
    svc.repo = repo
    svc.agent_svc = agent_svc
    svc.openrouter = openrouter
    svc.openviking = openviking
    svc.runner_url = ""
    svc.settings = settings
    svc._starting_locks = OrderedDict()
    return svc


# ── Quota enforcement tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_hosted_agent_enforces_quota_at_limit():
    """create_hosted_agent raises HTTP 409 when user already owns max_hosted agents.

    This tests backend enforcement, not just the frontend button-hide.
    max_hosted_agents_per_user defaults to 1 (free tier).
    """
    svc = _make_create_svc(existing_agent_count=1, max_hosted=1)

    with pytest.raises(HTTPException) as exc_info:
        await svc.create_hosted_agent(
            user_id="u1",
            user_email="user@example.com",
            name="My Second Agent",
            system_prompt="Do stuff",
        )

    assert exc_info.value.status_code == 409
    assert "1" in exc_info.value.detail  # mentions the limit
    # Crucially: agent_svc.register_agent was NOT called (quota check fires first)
    svc.agent_svc.register_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_hosted_agent_enforces_quota_above_limit():
    """Quota fires even when user somehow has more agents than limit (data inconsistency guard)."""
    svc = _make_create_svc(existing_agent_count=3, max_hosted=1)

    with pytest.raises(HTTPException) as exc_info:
        await svc.create_hosted_agent(
            user_id="u1",
            user_email="user@example.com",
            name="Overflow Agent",
            system_prompt="Do stuff",
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_create_hosted_agent_succeeds_when_under_quota():
    """create_hosted_agent succeeds when user has 0 agents and limit is 1."""
    svc = _make_create_svc(existing_agent_count=0, max_hosted=1)

    result = await svc.create_hosted_agent(
        user_id="u1",
        user_email="user@example.com",
        name="My First Agent",
        system_prompt="Be helpful",
    )

    assert result["agent_name"] == "My First Agent"
    svc.agent_svc.register_agent.assert_awaited_once()
    svc.repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_hosted_agent_quota_uses_count_by_owner():
    """Quota is checked via repo.count_by_owner, not a frontend-only gate."""
    svc = _make_create_svc(existing_agent_count=0, max_hosted=1)

    await svc.create_hosted_agent(
        user_id="u1",
        user_email="user@example.com",
        name="Test",
        system_prompt="Be helpful",
    )

    # count_by_owner must be called with the correct user_id
    svc.repo.count_by_owner.assert_awaited_once_with("u1")


@pytest.mark.asyncio
async def test_create_hosted_agent_multi_tenant_quota():
    """Two different users each under the limit can both create agents."""
    svc_u1 = _make_create_svc(existing_agent_count=0, max_hosted=1)
    svc_u2 = _make_create_svc(existing_agent_count=0, max_hosted=1)

    r1 = await svc_u1.create_hosted_agent(
        user_id="u1", user_email="u1@x.com", name="U1 Agent", system_prompt="go"
    )
    r2 = await svc_u2.create_hosted_agent(
        user_id="u2", user_email="u2@x.com", name="U2 Agent", system_prompt="go"
    )

    assert r1["agent_name"] == "U1 Agent"
    assert r2["agent_name"] == "U2 Agent"


@pytest.mark.asyncio
async def test_create_hosted_agent_model_not_available_rejected():
    """create_hosted_agent raises HTTP 400 if requested model is not on allowlist."""
    svc = _make_create_svc(existing_agent_count=0)
    svc.openrouter.is_allowed = AsyncMock(return_value=False)

    with pytest.raises(HTTPException) as exc_info:
        await svc.create_hosted_agent(
            user_id="u1",
            user_email="u@x.com",
            name="Agent",
            system_prompt="go",
            model="some/deprecated:model",
        )

    assert exc_info.value.status_code == 400
    svc.agent_svc.register_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_fork_hosted_agent_enforces_quota():
    """fork_hosted_agent also enforces the per-user quota (backend check)."""
    from app.services.hosted_agent_service import HostedAgentService

    repo = AsyncMock()
    repo.count_by_owner = AsyncMock(return_value=1)  # already at limit
    repo.get_public_by_id = AsyncMock(return_value={
        "id": "source-id",
        "agent_id": "source-agent-id",
        "owner_user_id": "other-user",
        "agent_name": "Source Agent",
        "agent_handle": "source",
        "system_prompt": "do stuff",
        "model": "test/model:free",
        "specialization": "programmer",
        "skills": [],
        "description": "",
    })
    repo.db = AsyncMock()

    openrouter = AsyncMock()
    openrouter.is_allowed = AsyncMock(return_value=True)

    settings = MagicMock()
    settings.max_hosted_agents_per_user = 1

    svc = HostedAgentService.__new__(HostedAgentService)
    svc.repo = repo
    svc.openrouter = openrouter
    svc.settings = settings
    svc._starting_locks = OrderedDict()

    with pytest.raises(HTTPException) as exc_info:
        await svc.fork_hosted_agent(
            source_hosted_id="source-id",
            user_id="u2",
            user_email="u2@x.com",
        )

    assert exc_info.value.status_code == 409

"""Unit tests for BUG-2 (update_agent return type) and BUG-3 (_file_response safe keys).

Also covers: BUG-4 (update_self_hosted_agent concurrent-delete guard — 404 not KeyError/500).
"""

from __future__ import annotations

from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.hosted_agents import _file_response
from app.main import app
from app.services.agent_service import get_agent_by_api_key
from app.services.hosted_agent_service import HostedAgentService, get_hosted_agent_service

# ─── BUG-3: _file_response on raw runner dicts ───────────────────────────────


def test_file_response_minimal_dict():
    """_file_response must not raise KeyError on a raw runner dict missing synthetic keys."""
    raw = {"file_path": "main.py", "content": "print('hello')"}
    result = _file_response(raw)
    assert result.id == ""
    assert result.file_type == "text"
    assert result.size_bytes == 0
    assert result.updated_at == ""
    assert result.version == ""
    assert result.truncated is False
    assert result.is_binary is False


def test_file_response_full_dict():
    """_file_response with all keys present uses actual values."""
    full = {
        "id": "abc-123",
        "file_path": "agent.yaml",
        "file_type": "yaml",
        "content": "name: test",
        "size_bytes": 9,
        "updated_at": "2026-01-01T00:00:00",
        "version": "sha256:cafe",
        "truncated": True,
        "is_binary": False,
    }
    result = _file_response(full)
    assert result.id == "abc-123"
    assert result.file_type == "yaml"
    assert result.size_bytes == 9
    assert result.updated_at == "2026-01-01T00:00:00"
    assert result.version == "sha256:cafe"
    assert result.truncated is True


def test_file_response_id_none():
    """_file_response with id=None (from _runner_file_to_dict sentinel) returns empty string."""
    d = {"id": None, "file_path": "x.txt"}
    result = _file_response(d)
    assert result.id == ""


# ─── BUG-2: update_agent return type ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_agent_returns_none_when_row_disappeared():
    """update_agent annotation is dict | None — callers must tolerate None."""
    hosted_row = {
        "id": "h1",
        "agent_id": "a1",
        "owner_user_id": "u1",
        "status": "stopped",
        "model": "test/model:free",
        "system_prompt": "hi",
        "runtime": "python-minimal",
    }

    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=dict(hosted_row))
    repo.update = AsyncMock(return_value=None)  # row disappeared mid-update

    openrouter = AsyncMock()
    openrouter.is_allowed = AsyncMock(return_value=True)

    settings = MagicMock()
    settings.agent_runner_url = "http://runner"
    settings.agent_runner_key = "key"
    settings.oauth_redirect_base_url = "https://agentspore.com"

    svc = HostedAgentService.__new__(HostedAgentService)
    svc.repo = repo
    svc.agent_svc = AsyncMock()
    svc.openrouter = openrouter
    svc.openviking = AsyncMock()
    svc.openviking.enabled = False
    svc.runner_url = "http://runner"
    svc.settings = settings
    svc._starting_locks = OrderedDict()

    # Should return None, not raise, when repo.update returns None
    with patch.object(svc, "get_hosted_agent", new_callable=AsyncMock, return_value=dict(hosted_row)):
        result = await svc.update_agent("h1", "u1", {"heartbeat_seconds": 120})

    assert result is None


# ─── BUG-4: update_self concurrent-delete guard ──────────────────────────────


@pytest.mark.asyncio
async def test_update_self_returns_404_when_row_disappears_after_update():
    """PATCH /hosted-agents/self must return 404 (not KeyError/500) when
    repo.get_by_id returns None after update — simulates concurrent delete.
    """
    hosted_row = {
        "id": "h-concurrent",
        "agent_id": "a-concurrent",
        "owner_user_id": "u-concurrent",
        "status": "stopped",
        "model": "test/model:free",
        "system_prompt": "hello",
        "runtime": "python-minimal",
        "memory_limit_mb": 256,
        "heartbeat_seconds": 60,
        "agent_name": "TestAgent",
        "agent_handle": "testagent",
        "skills": [],
        "specialization": "programmer",
        "description": "",
        "fork_count": 0,
        "forked_from_agent_id": None,
        "forked_from_agent_name": None,
        "is_public": False,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }

    mock_agent = {"id": "a-concurrent", "email": "test@example.com"}

    svc_mock = MagicMock()
    svc_mock.repo = AsyncMock()
    svc_mock.repo.get_by_agent_id = AsyncMock(return_value=dict(hosted_row))
    svc_mock.update_agent = AsyncMock(return_value=None)
    svc_mock.repo.get_by_id = AsyncMock(return_value=None)  # concurrent delete

    app.dependency_overrides[get_agent_by_api_key] = lambda: mock_agent
    app.dependency_overrides[get_hosted_agent_service] = lambda: svc_mock
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.patch(
                "/api/v1/hosted-agents/self",
                json={"heartbeat_seconds": 120},
                headers={"X-API-Key": "af_testkey"},
            )

        assert response.status_code == 404, (
            f"Expected 404 on concurrent delete, got {response.status_code}: {response.text}"
        )
    finally:
        app.dependency_overrides.clear()

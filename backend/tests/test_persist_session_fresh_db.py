"""Test HostedAgentService._persist_session opens a fresh DB session.

Regression: previously _persist_session reused self.db when spawned via
asyncio.create_task from send_owner_message. In the cron loop, this caused
asyncpg "another operation in progress" errors because the next iteration
of the for-loop hit self.db while the backgrounded persist task was still
holding it. Cron loop crashed → 0 tasks dispatched on prod.

Fix: _persist_session opens its own session via async_session_maker so the
caller's self.db is never touched by the background task.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_persist_session_opens_fresh_db_session():
    """_persist_session must use async_session_maker, not self.db."""
    from app.services.hosted_agent_service import HostedAgentService

    # Caller's session — must NOT be touched by _persist_session
    caller_db = AsyncMock(name="caller_db")
    caller_repo = AsyncMock(name="caller_repo")
    caller_repo.db = caller_db
    caller_repo.get_by_id = AsyncMock(name="caller_repo.get_by_id")

    # Build the service with caller's repo
    svc = HostedAgentService.__new__(HostedAgentService)
    svc.repo = caller_repo
    svc.agent_svc = AsyncMock()

    ov = AsyncMock()
    ov.enabled = False
    svc.openviking = ov
    svc.openrouter = AsyncMock()

    # Mock async_session_maker to return a fresh session each call
    fresh_db = AsyncMock(name="fresh_db")

    @asynccontextmanager
    async def fake_session_maker():
        yield fresh_db

    # Patch the import inside the function and patch _save_runner_history globally
    with patch("app.core.database.async_session_maker", fake_session_maker), \
         patch.object(HostedAgentService, "_save_runner_history", new=AsyncMock()) as save_hist:
        await svc._persist_session("hosted-1", "user msg", "agent reply")

    # Caller's get_by_id MUST NOT have been called by the background task
    caller_repo.get_by_id.assert_not_called()
    # save_runner_history called once on the locally-built service
    assert save_hist.call_count == 1


@pytest.mark.asyncio
async def test_persist_session_indexes_openviking_when_enabled():
    """When openviking.enabled is True, the exchange must be indexed via local repo."""
    from app.repositories.hosted_agent_repo import HostedAgentRepository
    from app.services.hosted_agent_service import HostedAgentService

    fresh_db = AsyncMock(name="fresh_db")

    @asynccontextmanager
    async def fake_session_maker():
        yield fresh_db

    # OpenViking with enabled=True so the index branch runs
    ov = MagicMock()
    ov.enabled = True
    ov.add_to_agent_session = AsyncMock()

    svc = HostedAgentService.__new__(HostedAgentService)
    svc.repo = AsyncMock()  # caller's repo, should NOT be used
    svc.agent_svc = AsyncMock()
    svc.openviking = ov
    svc.openrouter = AsyncMock()

    # Stub the locally-built repo via patching HostedAgentRepository.get_by_id
    with patch("app.core.database.async_session_maker", fake_session_maker), \
         patch.object(HostedAgentService, "_save_runner_history", new=AsyncMock()), \
         patch.object(HostedAgentRepository, "get_by_id", AsyncMock(return_value={"agent_id": "agent-7"})):
        await svc._persist_session("hosted-1", "u" * 600, "a" * 1500)

    # OpenViking indexed using the locally-fetched agent_id
    ov.add_to_agent_session.assert_awaited_once()
    args, _ = ov.add_to_agent_session.call_args
    agent_id, exchange = args
    assert agent_id == "agent-7"
    assert "User:" in exchange and "Agent:" in exchange
    # Confirm truncation: 500 + 1000 cap, prefix labels included
    assert len(exchange) <= 600 + 1500 + len("User: \nAgent: ")


@pytest.mark.asyncio
async def test_persist_session_swallows_openviking_errors():
    """OpenViking failures must not propagate — caller is fire-and-forget."""
    from app.repositories.hosted_agent_repo import HostedAgentRepository
    from app.services.hosted_agent_service import HostedAgentService

    fresh_db = AsyncMock(name="fresh_db")

    @asynccontextmanager
    async def fake_session_maker():
        yield fresh_db

    ov = MagicMock()
    ov.enabled = True
    ov.add_to_agent_session = AsyncMock(side_effect=RuntimeError("openviking down"))

    svc = HostedAgentService.__new__(HostedAgentService)
    svc.repo = AsyncMock()
    svc.agent_svc = AsyncMock()
    svc.openviking = ov
    svc.openrouter = AsyncMock()

    with patch("app.core.database.async_session_maker", fake_session_maker), \
         patch.object(HostedAgentService, "_save_runner_history", new=AsyncMock()), \
         patch.object(HostedAgentRepository, "get_by_id", AsyncMock(return_value={"agent_id": "a"})):
        # Must not raise
        await svc._persist_session("hosted-1", "u", "a")

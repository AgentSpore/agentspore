"""Cron auto-start must not mark a hosted agent 'running' on a failed start.

_call_runner silently no-ops ({}) when no runner is configured, and raises
only on an actual HTTP failure. Both paths must leave the row truthful: a
start that did not happen must not read 'running' afterward.
"""

from __future__ import annotations

from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.hosted_agent_service import HostedAgentService

pytestmark = pytest.mark.asyncio


def _make_svc(runner_url: str) -> tuple[HostedAgentService, dict]:
    hosted_row = {
        "id": "h1",
        "agent_id": "a1",
        "owner_user_id": "u1",
        "status": "stopped",
        "model": "test/model:free",
        "system_prompt": "hello",
        "runtime": "python-minimal",
        "memory_limit_mb": 256,
        "heartbeat_enabled": True,
        "heartbeat_seconds": 3600,
    }
    task = {
        "id": "task-1",
        "hosted_agent_id": "h1",
        "name": "hourly",
        "cron_expression": "0 * * * *",
        "auto_start": True,
        "agent_status": "stopped",
        "owner_user_id": "u1",
        "task_prompt": "do the thing",
        "scheduled_at": None,
        "max_runs": None,
        "run_count": 2454,
        "agent_handle": "h1",
        "agent_name": "hourly-bot",
    }

    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=dict(hosted_row))
    repo.update_status = AsyncMock()
    repo.get_session_history = AsyncMock(return_value=[])
    repo.get_due_cron_tasks = AsyncMock(return_value=[dict(task)])
    repo.mark_cron_run = AsyncMock()
    repo.update_cron_task = AsyncMock()
    repo.db = AsyncMock()
    lock_result = MagicMock()
    lock_result.scalar.return_value = True
    repo.db.execute = AsyncMock(return_value=lock_result)

    openrouter = AsyncMock()
    openrouter.resolve_model = AsyncMock(return_value="test/model:free")
    openrouter.get_context_length = AsyncMock(return_value=128_000)
    # resolve_provider is a SYNC method on the real service; leaving it an
    # AsyncMock returns an un-awaited coroutine that then gets subscripted
    # ("provider_info['base_url']") and crashes _start_agent_internal before
    # it ever reaches update_status — silently hiding whatever the mutation
    # under test does.
    openrouter.resolve_provider = MagicMock(return_value=None)

    openviking = AsyncMock()
    openviking.enabled = False

    settings = MagicMock()
    settings.agent_runner_url = runner_url
    settings.agent_runner_key = "test-key"
    settings.oauth_redirect_base_url = "https://agentspore.com"

    svc = HostedAgentService.__new__(HostedAgentService)
    svc.repo = repo
    svc.agent_svc = AsyncMock()
    svc.openrouter = openrouter
    svc.openviking = openviking
    svc.runner_url = runner_url
    svc.settings = settings
    svc._starting_locks = OrderedDict()
    return svc, hosted_row


async def test_cron_start_on_no_runner_configured_never_marks_running(monkeypatch):
    """No runner service on the host at all (agent_runner_url=""). Cron must
    record a failure and leave the row untouched — never 'running'.

    MUTATION: remove the `if not self.runner_url: raise HTTPException(503, ...)`
    guard in _start_agent_internal — update_status(..., "running", ...) fires
    unconditionally and this assertion goes red.
    """
    svc, _ = _make_svc(runner_url="")
    monkeypatch.setattr(
        "app.core.redis_client.get_redis", AsyncMock(return_value=AsyncMock())
    )

    executed = await svc.execute_due_cron_tasks()

    assert executed == 0
    running_calls = [
        c for c in svc.repo.update_status.await_args_list if c.args[1] == "running"
    ]
    assert running_calls == [], "a start that never happened must not read 'running'"
    svc.repo.mark_cron_run.assert_awaited_once()
    _, kwargs = svc.repo.mark_cron_run.call_args
    error = kwargs.get("error") if kwargs else svc.repo.mark_cron_run.call_args.args[-1]
    assert error is not None, "the failure must be recorded, not swallowed silently"


async def test_cron_start_on_runner_http_failure_never_marks_running(monkeypatch):
    """Runner IS configured but answers an HTTP error on start (e.g. 400
    'Agent already running' from a stale row, or 503). The row must still not
    end up 'running' from this call."""
    svc, _ = _make_svc(runner_url="http://runner")
    monkeypatch.setattr(
        "app.core.redis_client.get_redis", AsyncMock(return_value=AsyncMock())
    )

    resp = MagicMock()
    resp.status_code = 400
    resp.text = '{"detail":"Agent already running"}'
    request = MagicMock()

    async def _raise_start(*args, **kwargs):
        raise httpx.HTTPStatusError("bad", request=request, response=resp)

    svc._call_runner = AsyncMock(side_effect=_raise_start)

    executed = await svc.execute_due_cron_tasks()

    assert executed == 0
    svc.repo.update_status.assert_not_awaited()
    svc.repo.mark_cron_run.assert_awaited_once()

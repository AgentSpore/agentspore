"""Tests for HostedAgentService.ensure_running auto-lifecycle.

Unit tests only — no real DB or runner required.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.hosted_agent_service import (
    HostedAgentRunnerUnavailable,
    HostedAgentTooManyFailures,
)

# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_svc(hosted_status: str = "stopped", runner_url: str = "http://runner"):
    """Construct a minimal HostedAgentService with all external deps mocked."""
    from collections import OrderedDict

    from app.services.hosted_agent_service import HostedAgentService

    hosted_row = {
        "id": "h1",
        "agent_id": "a1",
        "owner_user_id": "u1",
        "status": hosted_status,
        "model": "test/model:free",
        "system_prompt": "hello",
        "runtime": "python-minimal",
        "memory_limit_mb": 256,
        "heartbeat_enabled": True,
        "heartbeat_seconds": 3600,
    }

    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=dict(hosted_row))
    repo.update_status = AsyncMock()
    repo.get_session_history = AsyncMock(return_value=[])
    repo.db = AsyncMock()
    # Advisory lock: returns True by default (lock acquired)
    _lock_result = MagicMock()
    _lock_result.scalar.return_value = True
    repo.db.execute = AsyncMock(return_value=_lock_result)

    agent_svc = AsyncMock()
    openrouter = AsyncMock()
    openrouter.resolve_model = AsyncMock(return_value="test/model:free")
    openrouter.get_context_length = AsyncMock(return_value=128000)

    settings = MagicMock()
    settings.agent_runner_url = runner_url
    settings.agent_runner_key = "test-key"
    settings.oauth_redirect_base_url = "https://agentspore.com"

    openviking = AsyncMock()
    openviking.enabled = False

    svc = HostedAgentService.__new__(HostedAgentService)
    svc.repo = repo
    svc.agent_svc = agent_svc
    svc.openrouter = openrouter
    svc.openviking = openviking
    svc.runner_url = runner_url
    svc.settings = settings
    svc._starting_locks = OrderedDict()

    return svc, hosted_row


def _make_mock_redis(failure_count: int = 0):
    """Return a mock Redis client with a configurable auto-start failure counter."""
    mock_redis = AsyncMock()
    raw = str(failure_count).encode() if failure_count else None
    mock_redis.get = AsyncMock(return_value=raw)
    mock_redis.delete = AsyncMock()
    mock_redis.incr = AsyncMock()
    mock_redis.expire = AsyncMock()
    return mock_redis


# ─── Unit tests: ensure_running ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_running_already_running():
    """Returns True immediately when agent is already running — no start call."""
    svc, hosted_row = _make_svc(hosted_status="running")

    mock_redis = _make_mock_redis()

    with patch.object(svc, "_start_agent_internal", new_callable=AsyncMock) as mock_start:
        with patch("app.core.redis_client.get_redis", return_value=mock_redis):
            result = await svc.ensure_running("h1", source="chat")

    assert result is True
    mock_start.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_running_cold_start():
    """Calls _start_agent_internal when agent is stopped. Returns False (cold-start)."""
    svc, hosted_row = _make_svc(hosted_status="stopped")

    mock_redis = _make_mock_redis()

    with patch.object(svc, "_start_agent_internal", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = {"status": "running", "message": "Agent started"}
        with patch("app.core.redis_client.get_redis", return_value=mock_redis):
            result = await svc.ensure_running("h1", source="cron")

    assert result is False
    mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_running_parallel_calls_start_once():
    """Two concurrent callers: only one _start_agent_internal call fires."""
    svc, hosted_row = _make_svc(hosted_status="stopped")

    start_count = 0

    async def slow_start(hosted, skip_bootstrap=False):
        nonlocal start_count
        start_count += 1
        await asyncio.sleep(0.05)  # simulate runner latency
        # After start, update DB mock to return running so second caller sees it
        svc.repo.get_by_id = AsyncMock(return_value={**hosted_row, "status": "running"})

    mock_redis = _make_mock_redis()

    with patch.object(svc, "_start_agent_internal", side_effect=slow_start):
        with patch("app.core.redis_client.get_redis", return_value=mock_redis):
            results = await asyncio.gather(
                svc.ensure_running("h1", source="chat"),
                svc.ensure_running("h1", source="chat"),
            )

    # Only one actual runner call
    assert start_count == 1
    # At least one result is False (cold-start by the winner)
    assert False in results


@pytest.mark.asyncio
async def test_ensure_running_too_many_failures():
    """Raises HostedAgentTooManyFailures when Redis counter >= 3."""
    svc, _ = _make_svc(hosted_status="stopped")
    mock_redis = _make_mock_redis(failure_count=3)

    with patch("app.core.redis_client.get_redis", return_value=mock_redis):
        with pytest.raises(HostedAgentTooManyFailures):
            await svc.ensure_running("h1", source="chat")


@pytest.mark.asyncio
async def test_ensure_running_runner_unavailable_increments_counter():
    """When _start_agent_internal raises 503, failure counter is incremented."""
    svc, _ = _make_svc(hosted_status="stopped")
    mock_redis = _make_mock_redis()

    async def failing_start(hosted, skip_bootstrap=False):
        raise HTTPException(503, "Runner is down")

    with patch.object(svc, "_start_agent_internal", side_effect=failing_start):
        with patch("app.core.redis_client.get_redis", return_value=mock_redis):
            with pytest.raises(HostedAgentRunnerUnavailable):
                await svc.ensure_running("h1", source="chat")

    mock_redis.incr.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_running_error_status_triggers_start():
    """An agent in 'error' status is treated like 'stopped' and auto-started."""
    svc, hosted_row = _make_svc(hosted_status="error")
    mock_redis = _make_mock_redis()

    with patch.object(svc, "_start_agent_internal", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = {"status": "running"}
        with patch("app.core.redis_client.get_redis", return_value=mock_redis):
            result = await svc.ensure_running("h1", source="ws_event")

    assert result is False
    mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_running_chat_source_skips_bootstrap():
    """source='chat' passes skip_bootstrap=True to _start_agent_internal."""
    svc, _ = _make_svc(hosted_status="stopped")
    mock_redis = _make_mock_redis()

    captured_kwargs: dict = {}

    async def capturing_start(hosted, skip_bootstrap=False):
        captured_kwargs["skip_bootstrap"] = skip_bootstrap

    with patch.object(svc, "_start_agent_internal", side_effect=capturing_start):
        with patch("app.core.redis_client.get_redis", return_value=mock_redis):
            await svc.ensure_running("h1", source="chat")

    assert captured_kwargs["skip_bootstrap"] is True


@pytest.mark.asyncio
async def test_ensure_running_non_chat_source_does_not_skip_bootstrap():
    """source='cron' does NOT skip bootstrap."""
    svc, _ = _make_svc(hosted_status="stopped")
    mock_redis = _make_mock_redis()

    captured_kwargs: dict = {}

    async def capturing_start(hosted, skip_bootstrap=False):
        captured_kwargs["skip_bootstrap"] = skip_bootstrap

    with patch.object(svc, "_start_agent_internal", side_effect=capturing_start):
        with patch("app.core.redis_client.get_redis", return_value=mock_redis):
            await svc.ensure_running("h1", source="cron")

    assert captured_kwargs["skip_bootstrap"] is False


# ─── BUG-1: redis unavailable → no UnboundLocalError ────────────────────────


@pytest.mark.asyncio
async def test_ensure_running_redis_unavailable_start_succeeds():
    """When Redis is down, ensure_running still cold-starts the agent without error."""
    svc, _ = _make_svc(hosted_status="stopped")

    with patch.object(svc, "_start_agent_internal", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = None
        with patch(
            "app.core.redis_client.get_redis",
            side_effect=ConnectionError("redis down"),
        ):
            result = await svc.ensure_running("h1", source="chat")

    assert result is False
    mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_running_redis_unavailable_http_start_fail_no_unbound():
    """When Redis is down AND _start_agent_internal raises HTTP 503 — no UnboundLocalError.
    Lock must be released (not dangling).
    """
    svc, _ = _make_svc(hosted_status="stopped")

    async def failing_start(hosted, skip_bootstrap=False):
        raise HTTPException(503, "runner gone")

    with patch.object(svc, "_start_agent_internal", side_effect=failing_start):
        with patch(
            "app.core.redis_client.get_redis",
            side_effect=ConnectionError("redis down"),
        ):
            with pytest.raises(HostedAgentRunnerUnavailable):
                await svc.ensure_running("h1", source="chat")

    # Lock must be released after the exception
    assert "h1" not in svc._starting_locks


@pytest.mark.asyncio
async def test_ensure_running_redis_unavailable_generic_start_fail_no_unbound():
    """When Redis is down AND _start_agent_internal raises a generic exception — no UnboundLocalError."""
    svc, _ = _make_svc(hosted_status="stopped")

    async def failing_start(hosted, skip_bootstrap=False):
        raise RuntimeError("container exploded")

    with patch.object(svc, "_start_agent_internal", side_effect=failing_start):
        with patch(
            "app.core.redis_client.get_redis",
            side_effect=ConnectionError("redis down"),
        ):
            with pytest.raises(HostedAgentRunnerUnavailable):
                await svc.ensure_running("h1", source="chat")

    assert "h1" not in svc._starting_locks


# ─── stream_owner_message phase events ──────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_owner_message_stopped_emits_starting_agent_phase():
    """stream_owner_message when agent is stopped emits starting_agent + agent_started phases."""
    svc, hosted_row = _make_svc(hosted_status="stopped", runner_url="http://runner")

    svc.repo.add_owner_message = AsyncMock(
        return_value={"id": "m1", "sender_type": "user", "content": "hi"}
    )

    async def mock_ensure(hosted_id, *, source):
        return False

    # Stub the httpx streaming portion to emit a single done event
    async def fake_stream_generator():
        yield json.dumps({"type": "done", "reply": "ok", "tool_calls": [], "thinking": None}) + "\n"

    events = []
    with patch.object(svc, "ensure_running", side_effect=mock_ensure):
        with patch.object(svc, "get_hosted_agent", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = dict(hosted_row)
            # Patch httpx so the runner call returns a minimal stream
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_resp = AsyncMock()
                mock_resp.status_code = 200
                mock_resp.aiter_lines = fake_stream_generator
                mock_cm = AsyncMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
                mock_cm.__aexit__ = AsyncMock(return_value=False)
                mock_client = AsyncMock()
                mock_client.stream = MagicMock(return_value=mock_cm)
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                async for line in svc.stream_owner_message("h1", "u1", "hi"):
                    if line.strip():
                        events.append(json.loads(line.strip()))

    phases = [e.get("phase") for e in events if e.get("type") == "phase"]
    assert "starting_agent" in phases
    assert "agent_started" in phases


@pytest.mark.asyncio
async def test_stream_owner_message_runner_unavailable_retryable_error():
    """stream_owner_message emits retryable error when runner is unavailable."""
    from app.services.hosted_agent_service import HostedAgentRunnerUnavailable

    svc, hosted_row = _make_svc(hosted_status="stopped")
    svc.repo.add_owner_message = AsyncMock(return_value={"id": "m1"})

    async def failing_ensure_unavail(hosted_id, *, source):
        raise HostedAgentRunnerUnavailable("runner down")

    events = []
    with patch.object(svc, "ensure_running", side_effect=failing_ensure_unavail):
        with patch.object(svc, "get_hosted_agent", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = dict(hosted_row)
            async for line in svc.stream_owner_message("h1", "u1", "hi"):
                if line.strip():
                    events.append(json.loads(line.strip()))

    # starting_agent phase was emitted before the failure
    phases = [e.get("phase") for e in events if e.get("type") == "phase"]
    assert "starting_agent" in phases

    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events
    assert error_events[0].get("retryable") is True


@pytest.mark.asyncio
async def test_stream_owner_message_too_many_failures_non_retryable():
    """stream_owner_message emits non-retryable error when failure limit exceeded."""
    svc, hosted_row = _make_svc(hosted_status="stopped")
    svc.repo.add_owner_message = AsyncMock(return_value={"id": "m1"})

    async def failing_ensure_too_many(hosted_id, *, source):
        raise HostedAgentTooManyFailures("too many")

    events = []
    with patch.object(svc, "ensure_running", side_effect=failing_ensure_too_many):
        with patch.object(svc, "get_hosted_agent", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = dict(hosted_row)
            async for line in svc.stream_owner_message("h1", "u1", "hi"):
                if line.strip():
                    events.append(json.loads(line.strip()))

    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events
    assert error_events[0].get("retryable") is False


# ─── deliver_event auto-wake ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deliver_event_wakeable_schedules_auto_wake():
    """deliver_event calls asyncio.create_task with _auto_wake_hosted for wakeable events."""
    from app.services import connection_manager as cm

    tasks_created: list = []

    # Reset the singleton manager for isolation
    original_manager = cm._manager
    cm._manager = None

    try:
        from app.services.connection_manager import deliver_event

        async def fake_send(agent_id, event):
            return False

        async def fake_webhook_deliver(agent_id, event):
            return False

        async def fake_auto_wake(agent_id, event):
            tasks_created.append(agent_id)

        with patch.object(cm, "_auto_wake_hosted", fake_auto_wake):
            with patch("app.services.connection_manager.AgentWebhookService.deliver", new_callable=AsyncMock, return_value=False):
                mgr = cm.get_connection_manager()
                mgr.send = AsyncMock(return_value=False)

                created_coros = []
                original_create_task = asyncio.create_task

                def capturing_create_task(coro, *args, **kwargs):
                    created_coros.append(coro.__qualname__ if hasattr(coro, "__qualname__") else str(coro))
                    return original_create_task(coro, *args, **kwargs)

                with patch("asyncio.create_task", side_effect=capturing_create_task):
                    await deliver_event("agent-dm", {"type": "dm", "content": "hello"})

                # give background task a chance to run
                await asyncio.sleep(0)

        assert any("auto_wake" in name for name in created_coros)
    finally:
        cm._manager = original_manager


@pytest.mark.asyncio
async def test_deliver_event_non_wakeable_no_auto_wake():
    """deliver_event does NOT schedule auto-wake for non-wakeable event types like 'ping'."""
    from app.services import connection_manager as cm

    original_manager = cm._manager
    cm._manager = None

    try:
        from app.services.connection_manager import deliver_event

        with patch("app.services.connection_manager.AgentWebhookService.deliver", new_callable=AsyncMock, return_value=False):
            mgr = cm.get_connection_manager()
            mgr.send = AsyncMock(return_value=False)

            created_coros = []
            original_create_task = asyncio.create_task

            def capturing_create_task(coro, *args, **kwargs):
                created_coros.append(coro.__qualname__ if hasattr(coro, "__qualname__") else str(coro))
                return original_create_task(coro, *args, **kwargs)

            with patch("asyncio.create_task", side_effect=capturing_create_task):
                await deliver_event("agent-x", {"type": "ping"})

        # No auto-wake task should be scheduled for "ping"
        assert not any("auto_wake" in name for name in created_coros)
    finally:
        cm._manager = original_manager

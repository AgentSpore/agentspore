"""Tests for replay_sampler.py — prod-trace 1% sampling for offline eval."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from config import RunnerSettings


def _make_settings(**overrides) -> RunnerSettings:
    defaults = {
        "runner_key": "test-runner-key",
        "replay_enabled": True,
        "replay_sample_rate": 1.0,  # 100% so we can test the post path
    }
    defaults.update(overrides)
    return RunnerSettings(**defaults)


async def _run_sample(settings: RunnerSettings, **kwargs) -> None:
    """Call maybe_sample inside a running event loop, then drain tasks."""
    from replay_sampler import maybe_sample

    params = {
        "hosted_agent_id": "abc123",
        "agent_handle": "redditscout",
        "model": "mistralai/mistral-nemo",
        "trace_id": "trace-001",
        "input_messages": [{"role": "user", "content": "hello"}],
        "output_text": "done",
        "tool_calls": [{"tool": "execute", "args": {}}],
        "started_at": time.monotonic() - 1.5,
        "status": "completed",
        "metadata": {},
        "settings": settings,
    }
    params.update(kwargs)
    maybe_sample(**params)
    # Give the fire-and-forget task a chance to run
    await asyncio.sleep(0)


class TestReplaySampler:
    @pytest.mark.asyncio
    async def test_sample_at_100pct_creates_task(self):
        """At 100% sample rate, _post_replay_case is called with correct payload."""
        settings = _make_settings(replay_sample_rate=1.0)
        posted: list[dict] = []

        async def fake_post(payload, _settings):
            posted.append(payload)

        with patch("replay_sampler._post_replay_case", side_effect=fake_post):
            await _run_sample(settings)

        assert len(posted) == 1
        assert posted[0]["agent_handle"] == "redditscout"
        assert posted[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_sample_at_0pct_skips(self):
        """At 0% sample rate, no task is created."""
        settings = _make_settings(replay_sample_rate=0.0)

        with patch("replay_sampler._post_replay_case") as mock_post:
            await _run_sample(settings)

        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_replay_disabled_skips(self):
        """When replay_enabled=False, nothing is sent regardless of sample rate."""
        settings = _make_settings(replay_enabled=False, replay_sample_rate=1.0)

        with patch("replay_sampler._post_replay_case") as mock_post:
            await _run_sample(settings)

        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_post_exception_does_not_raise(self):
        """Exception inside _post_replay_case is swallowed — never propagates."""
        settings = _make_settings(replay_sample_rate=1.0)

        async def exploding_post(payload, _settings):
            raise RuntimeError("network error")

        with patch("replay_sampler._post_replay_case", side_effect=exploding_post):
            # Should not raise
            await _run_sample(settings)

    @pytest.mark.asyncio
    async def test_payload_shape(self):
        """Verify POST payload contains all required fields with correct types."""
        settings = _make_settings(replay_sample_rate=1.0)
        captured: list[dict] = []

        async def capture_post(payload, _settings):
            captured.append(payload)

        with patch("replay_sampler._post_replay_case", side_effect=capture_post):
            await _run_sample(
                settings,
                hosted_agent_id="ha-uuid-001",
                agent_handle="testbot",
                model="gpt-4o-mini",
                trace_id="t-xyz",
                status="failed",
            )

        assert len(captured) == 1
        p = captured[0]
        assert p["hosted_agent_id"] == "ha-uuid-001"
        assert p["agent_handle"] == "testbot"
        assert p["model"] == "gpt-4o-mini"
        assert p["trace_id"] == "t-xyz"
        assert p["status"] == "failed"
        assert "duration_ms" in p
        assert isinstance(p["duration_ms"], int)
        assert p["duration_ms"] >= 0

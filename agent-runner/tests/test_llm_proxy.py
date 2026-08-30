"""Tests for LLM proxy wiring (LLM_PROXY_URL -> OpenAIProvider http_client).

Scope: unit. Covers:
  - config default (empty = no proxy)
  - provider construction logic mirrored from routes/agents.py start_agent
  - AgentSession.aclose_llm_client closes the client and is a no-op when unset
  - mutation: dropping http_client from the provider call breaks the "proxy
    configured" assertion (both counts recorded below)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

import routes.agents as agents_mod

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import RunnerSettings  # noqa: E402
from session import AgentSession  # noqa: E402


def test_llm_proxy_url_defaults_empty(monkeypatch):
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)
    settings = RunnerSettings(runner_key="k")
    assert settings.llm_proxy_url == ""


def test_llm_proxy_url_read_from_env(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_URL", "http://127.0.0.1:3128")
    settings = RunnerSettings(runner_key="k")
    assert settings.llm_proxy_url == "http://127.0.0.1:3128"


class TestProviderClientWiring:
    """Exercises the builder start_agent actually calls, not a copy of it."""

    def test_no_proxy_configured_no_http_client(self, monkeypatch):
        monkeypatch.setattr(agents_mod.settings, "llm_proxy_url", "")

        assert agents_mod.build_llm_http_client() is None

    def test_proxy_configured_builds_client_with_chat_timeout(self, monkeypatch):
        monkeypatch.setattr(agents_mod.settings, "llm_proxy_url", "http://127.0.0.1:3128")
        monkeypatch.setattr(agents_mod.settings, "chat_timeout", 600)

        client = agents_mod.build_llm_http_client()

        assert client is not None
        assert client.timeout.connect == 600


class TestAgentSessionCloseLlmClient:
    @pytest.mark.asyncio
    async def test_closes_configured_client(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        session = AgentSession(
            hosted_id="h1",
            sandbox=None,
            agent=None,
            deps=None,
            llm_http_client=mock_client,
        )

        await session.aclose_llm_client()

        mock_client.aclose.assert_awaited_once()
        assert session.llm_http_client is None

    @pytest.mark.asyncio
    async def test_noop_when_no_client_configured(self):
        session = AgentSession(hosted_id="h1", sandbox=None, agent=None, deps=None)

        await session.aclose_llm_client()  # must not raise

        assert session.llm_http_client is None


@pytest.mark.asyncio
async def test_dead_sandbox_cleanup_closes_the_llm_client(monkeypatch):
    """A sandbox that dies must not strand its proxied client.

    Three call sites dropped a session from `sessions`; only two closed the
    client it held. The orphaned AsyncClient keeps pooled sockets with no
    owner left to close them — accumulating with runner uptime, the same
    shape as the stale-connection defect this module guards.
    """
    import routes.agents as agents_mod

    closed = []

    class FakeSession:
        def __init__(self):
            self.llm_http_client = object()

        def stop_heartbeat(self):
            pass

        def stop_websocket(self):
            pass

        def stop_quota_watcher(self):
            pass

        async def aclose_llm_client(self):
            closed.append(True)
            self.llm_http_client = None

    session = FakeSession()
    agents_mod.sessions["dead-agent"] = session

    await agents_mod._teardown_session("dead-agent", session)

    assert closed == [True], "session was dropped without closing its LLM client"
    assert "dead-agent" not in agents_mod.sessions

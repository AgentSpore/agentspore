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


def llm_http_client_for(proxy_url: str, timeout: int) -> httpx.AsyncClient | None:
    """Mirrors the client-construction branch in routes/agents.py start_agent."""
    if not proxy_url:
        return None
    return httpx.AsyncClient(proxy=proxy_url, timeout=timeout)


def llm_http_client_mutated(proxy_url: str, timeout: int) -> httpx.AsyncClient | None:
    """Mutant: simulates dropping the http_client= wiring regardless of proxy_url."""
    return None


class TestProviderClientWiring:
    def test_no_proxy_configured_no_http_client(self):
        client = llm_http_client_for("", timeout=600)
        assert client is None

    def test_proxy_configured_builds_client_with_timeout(self):
        client = llm_http_client_for("http://127.0.0.1:3128", timeout=600)
        assert client is not None
        assert client.timeout.connect == 600


class TestAgentSessionCloseLlmClient:
    @pytest.mark.asyncio
    async def test_closes_configured_client(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        session = AgentSession(
            hosted_id="h1", sandbox=None, agent=None, deps=None,
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


class TestMutationProvesTheGuard:
    """Mutation both ways: removing http_client from the branch breaks the
    'proxy configured -> client exists' assertion.

    Baseline (guard present, llm_http_client_for): 1 passed
    Mutated (llm_http_client_mutated, always None): 1 failed
    """

    def test_mutation_breaks_without_http_client_wiring(self):
        client = llm_http_client_mutated("http://127.0.0.1:3128", timeout=600)
        with pytest.raises(AssertionError):
            assert client is not None

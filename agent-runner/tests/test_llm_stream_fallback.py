"""Tests for model-fallback on the streaming chat path (chat_stream).

Covers the specific claim in the task: a transient model failure that happens
before any ndjson event was yielded to the client falls over to the next
LLM_FALLBACK_CHAIN model and the client receives a "done" event, not "error".
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest
from pydantic_ai.providers.openai import OpenAIProvider

import routes.chat as chat_mod
from routes.chat import chat_stream
from schemas import ChatRequest
from session import AgentSession

_TEST_PROVIDER = OpenAIProvider(base_url="https://example.invalid/v1", api_key="test-key")


class FakeResult:
    def all_messages(self):
        return []

    def new_messages(self):
        return []

    @property
    def output(self):
        return "hello from fallback model"


@pytest.fixture
def session_factory():
    def build(agent):
        session = MagicMock(spec=AgentSession)
        session.message_history = []
        session.deps = None
        session.agent = agent
        session.agent_handle = "test-agent"
        session.model = "z-ai/glm-4.5-air:free"
        session.openai_provider = _TEST_PROVIDER
        session.chat_lock = asyncio.Lock()
        session.worker_pool = MagicMock()
        session.worker_pool.max_concurrent = 1
        session.touch = MagicMock()
        return session

    return build


async def _collect_events(response):
    events = []
    async for chunk in response.body_iterator:
        events.append(json.loads(chunk))
    return events


class TestStreamFallback:
    @pytest.mark.asyncio
    async def test_transient_iter_failure_falls_over_and_client_gets_done(self, monkeypatch, session_factory):
        """session.agent.iter() raises transiently (no node reached, nothing yielded);
        the retry loop inside iter's own _run_with_llm_retry exhausts, then chat_stream
        falls over to the next fallback model via agent.run() — client sees "done"."""
        monkeypatch.setattr("routes.chat.asyncio.sleep", _noop)
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["b/model-2"])
        monkeypatch.setattr(chat_mod, "use_agent_context", _noop_ctx)

        agent = MagicMock()

        def iter_raises(*args, **kwargs):
            raise RuntimeError("503 Service Unavailable")

        agent.iter = iter_raises

        async def run_ok(*args, **kwargs):
            assert kwargs.get("model") is not None  # must be the fallback model, not None
            return FakeResult()

        agent.run = run_ok

        session = session_factory(agent)
        monkeypatch.setitem(chat_mod.sessions, "test-hosted-id", session)

        response = await chat_stream("test-hosted-id", ChatRequest(content="hello"))
        events = await _collect_events(response)

        assert not any(e["type"] == "error" for e in events)
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["reply"] == "hello from fallback model"

    @pytest.mark.asyncio
    async def test_chain_exhausted_yields_error_event(self, monkeypatch, session_factory):
        """No fallback models configured — first failure is terminal, client gets 'error'."""
        monkeypatch.setattr("routes.chat.asyncio.sleep", _noop)
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: [])
        monkeypatch.setattr(chat_mod, "use_agent_context", _noop_ctx)

        agent = MagicMock()

        def iter_raises(*args, **kwargs):
            raise RuntimeError("503 Service Unavailable")

        agent.iter = iter_raises

        session = session_factory(agent)
        monkeypatch.setitem(chat_mod.sessions, "test-hosted-id", session)

        response = await chat_stream("test-hosted-id", ChatRequest(content="hello"))
        events = await _collect_events(response)

        assert any(e["type"] == "error" for e in events)
        assert not any(e["type"] == "done" for e in events)


async def _noop(*_args, **_kwargs):
    return None


@asynccontextmanager
async def _noop_ctx(*args, **kwargs):
    yield

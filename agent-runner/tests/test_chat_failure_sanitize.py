"""Failure-path history sanitize: a turn that raises must not leave an orphan
ToolCallPart in session history that poisons every subsequent turn.

Regression coverage for prod: an interrupted turn left message_history holding
a ToolCallPart with no matching ToolReturnPart; every following turn then
failed at the provider with "tool result's tool id ... not found" (13
consecutive HTTP 500s on one agent, measured in prod).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.providers.openai import OpenAIProvider

from routes.chat import chat_with_agent
from schemas import ChatRequest
from session import sanitize_history, sessions

_TEST_PROVIDER = OpenAIProvider(base_url="https://example.invalid/v1", api_key="test-key")


def _orphan_history() -> list:
    """History with a trailing orphan ToolCallPart — no matching ToolReturnPart."""
    return [
        ModelRequest(parts=[UserPromptPart(content="run curl")]),
        ModelResponse(parts=[ToolCallPart(tool_name="execute", args={"command": "curl x"}, tool_call_id="t1")]),
    ]


def _session():
    session = MagicMock()
    session.model = "current-model"
    session.openai_provider = _TEST_PROVIDER
    session.chat_lock = AsyncMock()
    session.agent_handle = None
    session.worker_pool.max_concurrent = 1
    return session


@pytest.fixture
def registered_session():
    session = _session()
    sessions["agent-1"] = session
    yield session
    sessions.pop("agent-1", None)


@pytest.mark.asyncio
async def test_failed_turn_sanitizes_orphan_history(monkeypatch, registered_session):
    monkeypatch.setattr("routes.chat.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("routes.chat._load_model_chain", lambda: [])

    session = registered_session
    session.message_history = _orphan_history()

    session.agent = MagicMock()
    session.agent.run = AsyncMock(side_effect=RuntimeError("boom: agent crashed mid tool-call"))

    with pytest.raises(HTTPException):
        await chat_with_agent("agent-1", ChatRequest(content="hi"))

    assert not any(
        isinstance(p, ToolCallPart)
        for msg in session.message_history
        if isinstance(msg, ModelResponse)
        for p in msg.parts
    )


@pytest.mark.asyncio
async def test_next_turn_succeeds_after_failed_turn_left_orphan(monkeypatch, registered_session):
    """The turn AFTER a failure must not hit the poisoned-history 400 shape:
    the orphan must be gone before the next agent.run() call sees history."""
    monkeypatch.setattr("routes.chat.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("routes.chat._load_model_chain", lambda: [])

    session = registered_session
    session.message_history = _orphan_history()

    seen_histories: list[list] = []

    async def agent_run(*_args, message_history=None, **_kwargs):
        seen_histories.append(list(message_history))
        if len(seen_histories) == 1:
            raise RuntimeError("boom: agent crashed mid tool-call")
        new_msgs = [
            ModelRequest(parts=[UserPromptPart(content="hi again")]),
            ModelResponse(parts=[TextPart(content="hello")]),
        ]
        result = MagicMock()
        result.output = "hello"
        result.all_messages.return_value = message_history + new_msgs
        result.new_messages.return_value = new_msgs
        return result

    session.agent = MagicMock()
    session.agent.run = AsyncMock(side_effect=agent_run)

    with pytest.raises(HTTPException):
        await chat_with_agent("agent-1", ChatRequest(content="hi"))

    # Next turn: history handed to agent.run() must carry no orphan ToolCallPart.
    resp = await chat_with_agent("agent-1", ChatRequest(content="hi again"))
    assert resp.reply == "hello"

    second_call_history = seen_histories[1]
    assert not any(
        isinstance(p, ToolCallPart)
        for msg in second_call_history
        if isinstance(msg, ModelResponse)
        for p in msg.parts
    )


def test_sanitize_history_confirms_orphan_is_real():
    """Guard the fixture itself: sanitize_history must actually drop this shape."""
    cleaned = sanitize_history(_orphan_history())
    assert len(cleaned) == 1
    assert not any(isinstance(p, ToolCallPart) for p in cleaned[-1].parts if hasattr(cleaned[-1], "parts"))


@pytest.mark.asyncio
@pytest.mark.parametrize("max_concurrent", [1, 4])
async def test_worker_pool_path_sanitizes_orphan_history(monkeypatch, registered_session, max_concurrent):
    """max_concurrent > 1 routes through the worker-pool branch (chat.py's
    _use_worker_pool needs max_concurrent > 1); max_concurrent == 1 stays on
    the legacy global-lock branch even with owner_session_id set. Both must
    sanitize on failure, on their own history object."""
    monkeypatch.setattr("routes.chat.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("routes.chat._load_model_chain", lambda: [])

    from session_worker import SessionWorker

    session = registered_session
    session.worker_pool.max_concurrent = max_concurrent
    worker = SessionWorker(session_id="owner-1", memory_dir="/tmp/m", checkpoint_dir="/tmp/c")
    worker.message_history = _orphan_history()
    session.worker_pool.acquire_slot = AsyncMock(return_value=worker)
    session.worker_pool.release_slot = MagicMock()
    session.message_history = _orphan_history()

    session.agent = MagicMock()
    session.agent.run = AsyncMock(side_effect=RuntimeError("boom: agent crashed mid tool-call"))

    req = ChatRequest(content="hi", owner_session_id="owner-1")
    with pytest.raises(HTTPException):
        await chat_with_agent("agent-1", req)

    live_history = worker.message_history if max_concurrent > 1 else session.message_history
    assert not any(
        isinstance(p, ToolCallPart)
        for msg in live_history
        if isinstance(msg, ModelResponse)
        for p in msg.parts
    )


@pytest.mark.asyncio
async def test_chat_stream_sanitizes_orphan_history_on_failure(monkeypatch, registered_session):
    """chat_stream has its own history handling (agent.iter(), not agent.run()).
    Driving generate() to exhaustion through its error branch must leave no
    orphan in the live history list (session.message_history here, since this
    exercises the legacy non-pool branch)."""
    from routes.chat import chat_stream

    monkeypatch.setattr("routes.chat.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("routes.chat._load_model_chain", lambda: [])

    session = registered_session
    session.message_history = _orphan_history()
    session.deps = MagicMock()
    session.agent = MagicMock()
    session.agent.iter = MagicMock(side_effect=RuntimeError("boom: agent crashed mid tool-call"))

    response = await chat_stream("agent-1", ChatRequest(content="hi"))
    async for _ in response.body_iterator:
        pass  # drain to exhaustion so generate()'s finally runs

    assert not any(
        isinstance(p, ToolCallPart)
        for msg in session.message_history
        if isinstance(msg, ModelResponse)
        for p in msg.parts
    )


@pytest.mark.asyncio
async def test_auto_react_approval_loop_failure_keeps_message_history_identity(monkeypatch):
    """chat.py's chat_stream binds `_history = session.message_history` by
    reference at stream setup (routes/chat.py:637) and mutates that same
    object in place on every write. _auto_react's success path REBINDS
    self.message_history to a new list object (`self.message_history =
    sanitize_history(...)`) instead of mutating in place. Once that rebind
    has happened (first agent.run() succeeds, deferred-tool-approval loop
    starts), any outstanding alias captured before the rebind — like
    chat_stream's `_history` — now points at the old, abandoned list.

    If the SECOND agent.run() (inside the approval loop) then raises,
    sanitize_history_in_place(self.message_history) on the failure branch
    cleans the freshly-rebound object, not the alias's stale one — the
    alias's orphan (if any) is never touched and diverges from the session's
    real history. Must fail pre-fix, pass post-fix (mutate self.message_history
    in place instead of rebinding)."""
    from pydantic_ai import DeferredToolRequests
    from session import AgentSession

    sandbox = MagicMock()
    agent = MagicMock()
    session = AgentSession(hosted_id="agent-1", sandbox=sandbox, agent=agent, deps=MagicMock())
    session.message_history = []

    # Mirrors routes/chat.py:637 — an external holder binds by reference
    # BEFORE _auto_react runs, same as a concurrent chat_stream call would.
    external_alias = session.message_history

    first_result = MagicMock()
    first_result.output = DeferredToolRequests(
        approvals=[ToolCallPart(tool_name="execute", args={"command": "echo hi"}, tool_call_id="t1")]
    )
    first_result.all_messages.return_value = [
        ModelRequest(parts=[UserPromptPart(content="react")]),
        ModelResponse(parts=[ToolCallPart(tool_name="execute", args={"command": "echo hi"}, tool_call_id="t1")]),
    ]

    agent.run = AsyncMock(
        side_effect=[first_result, RuntimeError("boom: agent crashed mid tool-call")]
    )

    await session._auto_react({"type": "dm"})

    assert external_alias is session.message_history, (
        "session.message_history was rebound to a new list object mid-run; "
        "an external alias (like chat_stream's _history) is now stale"
    )
    assert not any(
        isinstance(p, ToolCallPart)
        for msg in external_alias
        if isinstance(msg, ModelResponse)
        for p in msg.parts
    )


def test_sanitize_in_place_drops_history_when_sanitize_itself_raises(monkeypatch):
    """An unexpected bug in sanitize_history must not leave the orphan behind.

    sanitize_history() already handles its own known failure modes, so anything
    reaching the except is a real bug — and that is exactly when keeping a
    poisoned history is worst: every later turn 400s at the provider until the
    session is reset by hand. A dropped transcript is recoverable; a bricked
    session is not.
    """
    import session as session_mod

    history = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[ToolCallPart(tool_name="execute", args={}, tool_call_id="orphan")]),
    ]

    def boom(_messages):
        raise RuntimeError("unexpected bug inside sanitize_history")

    monkeypatch.setattr(session_mod, "sanitize_history", boom)
    session_mod.sanitize_history_in_place(history)

    assert history == [], "poisoned history survived a sanitize failure"

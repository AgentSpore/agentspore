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

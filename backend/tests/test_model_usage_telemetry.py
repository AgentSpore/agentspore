"""Unit tests for server-side model-usage telemetry.

Clients rarely report ``model_used``, so the server falls back to the agent's
hosted-agent model. All collaborators are mocked — no DB, no runner.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.agent_service import AgentService
from app.services.chat_service import ChatService

AGENT = {"id": uuid4(), "name": "Tester", "specialization": "qa", "handle": "tester"}


@pytest.fixture()
def agent_svc() -> AgentService:
    """AgentService with a mocked repository and savepoint-capable session."""
    svc = AgentService.__new__(AgentService)
    svc.repo = AsyncMock()
    svc.repo.db = MagicMock()
    return svc


@pytest.fixture()
def chat_svc(agent_svc: AgentService) -> ChatService:
    """ChatService with mocked repo/redis and a real (mocked-repo) AgentService."""
    svc = ChatService.__new__(ChatService)
    svc.repo = AsyncMock()
    svc.repo.insert_agent_message = AsyncMock(
        return_value={"id": uuid4(), "created_at": "2026-07-25T00:00:00"},
    )
    svc.redis = AsyncMock()
    svc.agent_svc = agent_svc
    return svc


# ── record_model_usage ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reported_model_wins(agent_svc: AgentService) -> None:
    """Client-supplied model is authoritative — no hosted lookup at all."""
    ref_id = uuid4()

    await agent_svc.record_model_usage(
        AGENT["id"], "external/model-x", "chat", ref_id, "chat_message",
    )

    agent_svc.repo.get_hosted_agent_model.assert_not_called()
    agent_svc.repo.insert_model_usage.assert_awaited_once_with(
        AGENT["id"], "external/model-x", "chat", ref_id, "chat_message",
    )


@pytest.mark.asyncio
async def test_falls_back_to_hosted_model(agent_svc: AgentService) -> None:
    """No reported model + hosted agent → the hosted model is recorded."""
    agent_svc.repo.get_hosted_agent_model = AsyncMock(return_value="zai/glm-4.5-flash")
    ref_id = uuid4()

    await agent_svc.record_model_usage(AGENT["id"], None, "review", ref_id, "review")

    agent_svc.repo.insert_model_usage.assert_awaited_once_with(
        AGENT["id"], "zai/glm-4.5-flash", "review", ref_id, "review",
    )


@pytest.mark.asyncio
async def test_external_agent_records_nothing(agent_svc: AgentService) -> None:
    """Not hosted and nothing reported → no usage row, no error."""
    agent_svc.repo.get_hosted_agent_model = AsyncMock(return_value=None)

    await agent_svc.record_model_usage(AGENT["id"], None, "chat", uuid4(), "chat_message")

    agent_svc.repo.insert_model_usage.assert_not_called()


@pytest.mark.asyncio
async def test_insert_failure_is_swallowed(agent_svc: AgentService) -> None:
    """A failing usage insert must not propagate to the caller."""
    agent_svc.repo.insert_model_usage = AsyncMock(side_effect=RuntimeError("db down"))

    await agent_svc.record_model_usage(AGENT["id"], "model-x", "chat", uuid4(), "chat_message")


# ── chat send path ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_agent_message_records_hosted_model(chat_svc: ChatService) -> None:
    """A message without model_used still yields a usage row for a hosted agent."""
    hosted = "openai/gpt-oss-120b:free"
    chat_svc.agent_svc.repo.get_hosted_agent_model = AsyncMock(return_value=hosted)

    result = await chat_svc.send_agent_message(AGENT, "hello", "text", None)

    assert result["status"] == "ok"
    args = chat_svc.agent_svc.repo.insert_model_usage.await_args.args
    assert args[1] == hosted
    assert args[2] == "chat"
    assert args[4] == "chat_message"


@pytest.mark.asyncio
async def test_send_agent_message_survives_telemetry_failure(chat_svc: ChatService) -> None:
    """Telemetry blowing up must not fail the message send."""
    chat_svc.agent_svc.repo.get_hosted_agent_model = AsyncMock(side_effect=RuntimeError("db down"))

    result = await chat_svc.send_agent_message(AGENT, "hello", "text", None)

    assert result["status"] == "ok"
    chat_svc.repo.db.commit.assert_awaited()

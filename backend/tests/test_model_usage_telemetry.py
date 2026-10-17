"""Unit tests for server-side model-usage telemetry.

Only client-reported models are recorded. All collaborators are mocked —
no DB, no runner.
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
        AGENT["id"],
        "external/model-x",
        "chat",
        ref_id,
        "chat_message",
    )

    agent_svc.repo.insert_model_usage.assert_awaited_once_with(
        AGENT["id"],
        "external/model-x",
        "chat",
        ref_id,
        "chat_message",
    )


@pytest.mark.asyncio
async def test_external_agent_records_nothing(agent_svc: AgentService) -> None:
    """Nothing reported → no usage row, no error."""

    await agent_svc.record_model_usage(AGENT["id"], None, "chat", uuid4(), "chat_message")

    agent_svc.repo.insert_model_usage.assert_not_called()


@pytest.mark.asyncio
async def test_insert_failure_is_swallowed(agent_svc: AgentService) -> None:
    """A failing usage insert must not propagate to the caller."""
    agent_svc.repo.insert_model_usage = AsyncMock(side_effect=RuntimeError("db down"))

    await agent_svc.record_model_usage(AGENT["id"], "model-x", "chat", uuid4(), "chat_message")


# ── chat send path ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_agent_message_records_reported_model(chat_svc: ChatService) -> None:
    """A message records the model supplied by the external agent."""
    reported = "external/model-x"

    result = await chat_svc.send_agent_message(AGENT, "hello", "text", reported)

    assert result["status"] == "ok"
    args = chat_svc.agent_svc.repo.insert_model_usage.await_args.args
    assert args[1] == reported
    assert args[2] == "chat"
    assert args[4] == "chat_message"


@pytest.mark.asyncio
async def test_send_agent_message_survives_telemetry_failure(chat_svc: ChatService) -> None:
    """Telemetry blowing up must not fail the message send."""
    chat_svc.agent_svc.repo.insert_model_usage = AsyncMock(side_effect=RuntimeError("db down"))

    result = await chat_svc.send_agent_message(AGENT, "hello", "text", "external/model-x")

    assert result["status"] == "ok"
    chat_svc.repo.db.commit.assert_awaited()

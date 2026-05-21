"""Unit tests: AgentSession carries agent_handle + model for span attributes.

Covers:
- AgentSession round-trip: fields survive construction
- chat_with_agent calls use_agent_context with all 3 identifiers
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAgentSessionFields:
    def test_default_fields_are_empty_strings(self):
        """AgentSession without explicit agent_handle/model defaults to empty string."""
        from session import AgentSession

        session = AgentSession(
            hosted_id="host-1",
            sandbox=MagicMock(),
            agent=MagicMock(),
            deps=MagicMock(),
        )
        assert session.agent_handle == ""
        assert session.model == ""

    def test_fields_round_trip(self):
        """AgentSession stores agent_handle and model verbatim."""
        from session import AgentSession

        session = AgentSession(
            hosted_id="x",
            sandbox=MagicMock(),
            agent=MagicMock(),
            deps=MagicMock(),
            agent_handle="rsbuilderagent",
            model="z-ai/glm-4.5-air:free",
        )
        assert session.agent_handle == "rsbuilderagent"
        assert session.model == "z-ai/glm-4.5-air:free"


class TestChatUsesSpanAttrs:
    @pytest.mark.asyncio
    async def test_chat_passes_agent_handle_and_model_to_span(self):
        """chat_with_agent must call use_agent_context with handle and model from session."""
        from session import AgentSession

        mock_result = MagicMock()
        mock_result.output = "hello"
        mock_result.all_messages.return_value = []

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        session = AgentSession(
            hosted_id="agent-42",
            sandbox=MagicMock(),
            agent=mock_agent,
            deps=MagicMock(),
            agent_handle="mybot",
            model="gpt-4o-mini",
        )

        captured_kwargs: dict = {}

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_use_agent_context(**kwargs):
            captured_kwargs.update(kwargs)
            yield

        with patch("routes.chat.sessions", {"agent-42": session}), \
             patch("routes.chat.use_agent_context", fake_use_agent_context), \
             patch("routes.chat.sanitize_history", side_effect=lambda x: x):
            from fastapi.testclient import TestClient
            from fastapi import FastAPI
            from routes.chat import router

            app = FastAPI()
            app.include_router(router)

            # Use httpx directly to avoid sync/async mismatch in TestClient
            from httpx import AsyncClient, ASGITransport

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/agents/agent-42/chat",
                    json={"content": "ping"},
                )

        assert response.status_code == 200
        assert captured_kwargs["agent_id"] == "agent-42"
        assert captured_kwargs["agent_handle"] == "mybot"
        assert captured_kwargs["model"] == "gpt-4o-mini"

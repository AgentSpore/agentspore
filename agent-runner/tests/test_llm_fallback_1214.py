"""Tests for Z.AI error 1214 ("messages parameter is illegal") fallback behaviour.

Covers two layers:
  - llm_fallback.call_with_fallback: should continue to next model on 1214,
    not raise FallbackError immediately.
  - routes.chat._is_history_shape_error: correctly classifies the exception.
"""
from __future__ import annotations

import json
import pytest
import httpx

from unittest.mock import AsyncMock, patch, MagicMock


# ---------------------------------------------------------------------------
# llm_fallback layer
# ---------------------------------------------------------------------------

class TestRetryableProviderErrorCodes:
    def test_1214_in_retryable_codes(self):
        from llm_fallback import RETRYABLE_PROVIDER_ERROR_CODES
        assert 1214 in RETRYABLE_PROVIDER_ERROR_CODES

    def test_messages_parameter_illegal_in_patterns(self):
        from llm_fallback import RETRYABLE_ERROR_PATTERNS
        assert any("messages parameter is illegal" in p for p in RETRYABLE_ERROR_PATTERNS)


class TestCallWithFallback1214:
    """call_with_fallback should fall through to next model on error 1214."""

    @pytest.mark.asyncio
    async def test_1214_triggers_fallback_not_immediate_raise(self, monkeypatch):
        """HTTP 400 with error code 1214 in body → continue to next model."""
        from llm_fallback import call_with_fallback, FallbackError

        # Two providers: first returns 1214, second succeeds.
        responses = [
            # First model: 200 OK but error body with code 1214
            MagicMock(
                status_code=200,
                json=MagicMock(return_value={
                    "error": {
                        "code": 1214,
                        "message": "messages parameter is illegal: trailing assistant message",
                    }
                }),
            ),
            # Second model: success
            MagicMock(
                status_code=200,
                json=MagicMock(return_value={
                    "choices": [{"message": {"content": "hello"}}],
                    "model": "fallback-model",
                }),
            ),
        ]
        call_count = 0

        async def fake_post(*args, **kwargs):
            nonlocal call_count
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        monkeypatch.setenv("LLM_FALLBACK_CHAIN", "openrouter:z-ai/glm-4.5-air:free,openrouter:google/gemma-4-31b-it:free")

        # Patch provider to be active for both
        import providers as prov_mod

        class FakeProvider:
            is_active = True
            api_key = "test-key"
            base_url = "https://openrouter.ai/api/v1"

        fake_provider = FakeProvider()
        monkeypatch.setitem(prov_mod.PROVIDER_BY_NAME, "openrouter", fake_provider)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=fake_post)
            mock_client_cls.return_value = mock_client

            result = await call_with_fallback(messages=[{"role": "user", "content": "hi"}])

        assert call_count == 2, "Should have tried second model after 1214"
        assert "choices" in result

    @pytest.mark.asyncio
    async def test_1214_exhausts_chain_raises_fallback_error(self, monkeypatch):
        """If ALL models return 1214, FallbackError is raised after chain exhausted."""
        from llm_fallback import call_with_fallback, FallbackError

        error_body = {
            "error": {
                "code": 1214,
                "message": "messages parameter is illegal: trailing assistant message",
            }
        }

        async def fake_post(*args, **kwargs):
            return MagicMock(status_code=200, json=MagicMock(return_value=error_body))

        monkeypatch.setenv("LLM_FALLBACK_CHAIN", "openrouter:model-a,openrouter:model-b")

        import providers as prov_mod

        class FakeProvider:
            is_active = True
            api_key = "test-key"
            base_url = "https://openrouter.ai/api/v1"

        monkeypatch.setitem(prov_mod.PROVIDER_BY_NAME, "openrouter", FakeProvider())

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=fake_post)
            mock_client_cls.return_value = mock_client

            with pytest.raises(FallbackError) as exc_info:
                await call_with_fallback(messages=[{"role": "user", "content": "hi"}])

        assert len(exc_info.value.attempts) == 2
        assert all("1214" in a["error"] for a in exc_info.value.attempts)

    @pytest.mark.asyncio
    async def test_messages_parameter_illegal_message_triggers_fallback(self, monkeypatch):
        """Error body matching RETRYABLE_ERROR_PATTERNS by message text → fallback."""
        from llm_fallback import call_with_fallback, FallbackError

        call_count = 0
        responses = [
            MagicMock(
                status_code=200,
                json=MagicMock(return_value={
                    "error": {
                        "code": 400,  # Different code, but message matches pattern
                        "message": "messages parameter is illegal: last role must be user",
                    }
                }),
            ),
            MagicMock(
                status_code=200,
                json=MagicMock(return_value={
                    "choices": [{"message": {"content": "ok"}}],
                }),
            ),
        ]

        async def fake_post(*args, **kwargs):
            nonlocal call_count
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        monkeypatch.setenv("LLM_FALLBACK_CHAIN", "openrouter:model-a,openrouter:model-b")

        import providers as prov_mod

        class FakeProvider:
            is_active = True
            api_key = "test-key"
            base_url = "https://openrouter.ai/api/v1"

        monkeypatch.setitem(prov_mod.PROVIDER_BY_NAME, "openrouter", FakeProvider())

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=fake_post)
            mock_client_cls.return_value = mock_client

            result = await call_with_fallback(messages=[{"role": "user", "content": "hi"}])

        assert call_count == 2
        assert "choices" in result


# ---------------------------------------------------------------------------
# routes.chat layer
# ---------------------------------------------------------------------------

class TestIsHistoryShapeError:
    def test_1214_in_message(self):
        from routes.chat import _is_history_shape_error
        exc = RuntimeError("status_code: 400, model_name: z-ai/glm-4.5-air, body: {'error': {'code': 1214, 'message': 'messages parameter is illegal: trailing assistant message'}}")
        assert _is_history_shape_error(exc) is True

    def test_messages_parameter_illegal_substring(self):
        from routes.chat import _is_history_shape_error
        exc = RuntimeError("messages parameter is illegal: last message must be from user")
        assert _is_history_shape_error(exc) is True

    def test_unrelated_error_not_shape_error(self):
        from routes.chat import _is_history_shape_error
        exc = ValueError("Tool 'execute' returned invalid output")
        assert _is_history_shape_error(exc) is False

    def test_unprocessed_tool_calls_not_shape_error(self):
        from routes.chat import _is_history_shape_error
        exc = RuntimeError("unprocessed tool calls in history")
        assert _is_history_shape_error(exc) is False

    def test_transient_502_not_shape_error(self):
        from routes.chat import _is_history_shape_error
        exc = RuntimeError("502 Bad Gateway")
        assert _is_history_shape_error(exc) is False


class TestChatWith1214Retry:
    """chat_with_agent clears history and retries when model rejects message shape."""

    @pytest.mark.asyncio
    async def test_1214_clears_history_and_retries(self, monkeypatch):
        """On 1214 error, session.message_history is cleared and agent.run() retried."""
        import routes.chat as chat_mod
        from routes.chat import chat_with_agent
        from session import AgentSession

        # Fake session
        class FakeResult:
            def all_messages(self):
                return []

            def new_messages(self):
                return []

            @property
            def output(self):
                return "hello from fallback model"

        call_count = 0
        history_at_first_call = None

        async def fake_run(content, *, deps, message_history, model_settings):
            nonlocal call_count, history_at_first_call
            call_count += 1
            if call_count == 1:
                history_at_first_call = list(message_history)
                raise RuntimeError(
                    "status_code: 400, model_name: z-ai/glm-4.5-air, "
                    "body: {'error': {'code': 1214, 'message': 'messages parameter is illegal: trailing assistant'}}"
                )
            return FakeResult()

        fake_agent = MagicMock()
        fake_agent.run = fake_run

        session = MagicMock(spec=AgentSession)
        session.message_history = [{"role": "user", "content": "old message"}]
        session.deps = None
        session.agent = fake_agent
        session.agent_handle = "test-agent"
        session.model = "z-ai/glm-4.5-air:free"
        session.chat_lock = AsyncMock()
        session.chat_lock.locked = MagicMock(return_value=False)
        session.touch = MagicMock()

        monkeypatch.setitem(chat_mod.sessions, "test-hosted-id", session)

        # Patch use_agent_context to a no-op context manager
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _noop_ctx(*args, **kwargs):
            yield

        monkeypatch.setattr(chat_mod, "use_agent_context", _noop_ctx)

        from schemas import ChatRequest
        req = ChatRequest(content="hello")
        result = await chat_with_agent("test-hosted-id", req)

        assert call_count == 2, "Should retry after clearing history"
        assert history_at_first_call == [{"role": "user", "content": "old message"}]
        assert session.message_history == [], "History must be cleared after 1214"

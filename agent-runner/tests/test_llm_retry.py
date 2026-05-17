"""Tests for _is_transient_llm_error and _run_with_llm_retry in routes/chat.py.

These cover the upstream LLM flakiness retry logic: OpenRouter/Nemotron sometimes
returns 200 OK with a NULL body → pydantic ChatCompletion validation raises an
error that should be retried, not bubbled up as a 500.
"""
from __future__ import annotations

import asyncio

import pytest

from routes.chat import _is_transient_llm_error, _run_with_llm_retry


# ---------------------------------------------------------------------------
# _is_transient_llm_error
# ---------------------------------------------------------------------------

class TestIsTransientLLMError:
    def test_invalid_response_marker(self):
        exc = RuntimeError("Invalid response from openai chat completions endpoint")
        assert _is_transient_llm_error(exc) is True

    def test_chat_completion_validation_errors(self):
        exc = RuntimeError("4 validation errors for ChatCompletion: id Input should be valid string")
        assert _is_transient_llm_error(exc) is True

    def test_input_should_be_a_valid(self):
        exc = ValueError("Input should be a valid list [input_value=None]")
        assert _is_transient_llm_error(exc) is True

    def test_502_bad_gateway(self):
        exc = RuntimeError("HTTP 502 Bad Gateway from upstream")
        assert _is_transient_llm_error(exc) is True

    def test_503_service_unavailable(self):
        exc = RuntimeError("503 Service Unavailable")
        assert _is_transient_llm_error(exc) is True

    def test_504_gateway_timeout(self):
        exc = RuntimeError("504 Gateway Timeout")
        assert _is_transient_llm_error(exc) is True

    def test_unrelated_error_not_transient(self):
        exc = ValueError("Tool 'execute' returned invalid output")
        assert _is_transient_llm_error(exc) is False

    def test_unprocessed_tool_calls_not_transient(self):
        # Important: this is handled by separate history-clear logic, not retry
        exc = RuntimeError("unprocessed tool calls in history")
        assert _is_transient_llm_error(exc) is False

    def test_401_unauthorized_not_transient(self):
        exc = RuntimeError("401 Unauthorized: bad API key")
        assert _is_transient_llm_error(exc) is False


# ---------------------------------------------------------------------------
# _run_with_llm_retry
# ---------------------------------------------------------------------------

class TestRunWithLLMRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        calls = []

        async def factory_body():
            calls.append(1)
            return "ok"

        result = await _run_with_llm_retry(lambda: factory_body())
        assert result == "ok"
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_retries_on_transient_then_success(self, monkeypatch):
        async def _no_sleep(delay):
            return None
        monkeypatch.setattr("routes.chat.asyncio.sleep", _no_sleep)
        attempts = []

        async def factory_body():
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("Invalid response from openai chat completions endpoint")
            return "ok"

        result = await _run_with_llm_retry(lambda: factory_body(), max_attempts=3, base_delay=0.001)
        assert result == "ok"
        assert len(attempts) == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts_exhausted(self, monkeypatch):
        async def _no_sleep(delay):
            return None
        monkeypatch.setattr("routes.chat.asyncio.sleep", _no_sleep)
        attempts = []

        async def factory_body():
            attempts.append(1)
            raise RuntimeError("Invalid response from openai chat completions endpoint")

        with pytest.raises(RuntimeError, match="Invalid response"):
            await _run_with_llm_retry(lambda: factory_body(), max_attempts=3, base_delay=0.001)
        assert len(attempts) == 3

    @pytest.mark.asyncio
    async def test_non_transient_error_propagates_immediately(self, monkeypatch):
        async def _no_sleep(delay):
            return None
        monkeypatch.setattr("routes.chat.asyncio.sleep", _no_sleep)
        attempts = []

        async def factory_body():
            attempts.append(1)
            raise ValueError("non-transient bug in tool")

        with pytest.raises(ValueError, match="non-transient"):
            await _run_with_llm_retry(lambda: factory_body(), max_attempts=3, base_delay=0.001)
        assert len(attempts) == 1  # No retry — propagates on first failure

    @pytest.mark.asyncio
    async def test_factory_pattern_allows_re_awaiting(self):
        """coro_factory must return a NEW coroutine each call so retries work.

        Bare coroutines can only be awaited once; the factory pattern (lambda
        returning a fresh coro) is what makes this work.
        """
        attempts = []

        async def factory_body():
            attempts.append(1)
            if len(attempts) < 2:
                raise RuntimeError("503 Service Unavailable")
            return {"result": "fresh"}

        result = await _run_with_llm_retry(
            lambda: factory_body(),
            max_attempts=3,
            base_delay=0.001,
        )
        assert result == {"result": "fresh"}
        assert len(attempts) == 2

    @pytest.mark.asyncio
    async def test_backoff_doubles(self, monkeypatch):
        """Exponential backoff: 1s, 2s, 4s for max_attempts=4."""
        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("routes.chat.asyncio.sleep", fake_sleep)

        async def always_transient():
            raise RuntimeError("Invalid response from upstream")

        with pytest.raises(RuntimeError):
            await _run_with_llm_retry(lambda: always_transient(), max_attempts=4, base_delay=1.0)

        # 3 sleeps between 4 attempts: 1s, 2s, 4s
        assert sleeps == [1.0, 2.0, 4.0]

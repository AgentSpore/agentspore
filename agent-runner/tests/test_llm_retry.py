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

    def test_openai_sdk_rate_limit_error(self):
        # Shape raised by the openai SDK: openai.RateLimitError
        exc = RuntimeError(
            "Error code: 429 - {'error': {'code': '1302', "
            "'message': 'Rate limit reached for requests'}}"
        )
        assert _is_transient_llm_error(exc) is True

    def test_zai_1302_rate_limit_message(self):
        exc = RuntimeError("API error: Rate limit reached for requests")
        assert _is_transient_llm_error(exc) is True

    def test_httpx_429_status_line(self):
        exc = RuntimeError("Server error '429 Too Many Requests' for url 'https://api.z.ai/...'")
        assert _is_transient_llm_error(exc) is True

    def test_rate_limit_exceeded_error_code(self):
        exc = RuntimeError("{'error': {'code': 'rate_limit_exceeded'}}")
        assert _is_transient_llm_error(exc) is True

    def test_zai_1113_insufficient_balance_not_transient(self):
        """1113 arrives as HTTP 429 but is PERMANENT — the account cannot pay.

        Only the JSON error code separates it from a real rate limit (1302), so
        matching the 429 status line alone would burn the full backoff on a
        request that can never succeed.
        """
        exc = RuntimeError(
            "Error code: 429 - {'error': {'code': '1113', "
            "'message': 'Insufficient balance or no resource package. Please recharge'}}"
        )
        assert _is_transient_llm_error(exc) is False

    def test_zai_1113_unquoted_code_not_transient(self):
        exc = RuntimeError('Error code: 429 - {"error": {"code": 1113}}')
        assert _is_transient_llm_error(exc) is False

    def test_insufficient_balance_text_not_transient(self):
        exc = RuntimeError("429 Too Many Requests: Insufficient balance, please recharge")
        assert _is_transient_llm_error(exc) is False

    def test_1302_still_retries_while_1113_does_not(self):
        """The two 429 flavours must diverge — this is the whole point of the split."""
        rate_limited = RuntimeError(
            "Error code: 429 - {'error': {'code': '1302', "
            "'message': 'Rate limit reached for requests'}}"
        )
        no_balance = RuntimeError(
            "Error code: 429 - {'error': {'code': '1113', "
            "'message': 'Insufficient balance or no resource package'}}"
        )
        assert _is_transient_llm_error(rate_limited) is True
        assert _is_transient_llm_error(no_balance) is False

    def test_bare_429_digits_not_transient(self):
        # Guard against a bare "429" substring marker: token counts and ids must
        # not be mistaken for a rate-limit error.
        exc = ValueError("Tool output truncated at 1429 tokens")
        assert _is_transient_llm_error(exc) is False

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
    async def test_rate_limit_429_retries_with_backoff(self, monkeypatch):
        """A 429 from the free GLM tier must back off and retry, not raise a 500.

        Z.AI serves ~3 concurrent requests; the 4th gets 429 until one drains.
        Retrying IS the resilience — there is no second provider to fall over to.
        """
        delays: list[float] = []

        async def _record_sleep(delay):
            delays.append(delay)

        monkeypatch.setattr("routes.chat.asyncio.sleep", _record_sleep)
        attempts = []

        async def factory_body():
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError(
                    "Error code: 429 - {'error': {'code': '1302', "
                    "'message': 'Rate limit reached for requests'}}"
                )
            return "ok"

        result = await _run_with_llm_retry(lambda: factory_body())

        assert result == "ok"
        assert len(attempts) == 3
        # Exponential growth with equal jitter over [0.5x, 1.0x] of 1s then 2s.
        assert len(delays) == 2
        assert 0.5 <= delays[0] <= 1.0
        assert 1.0 <= delays[1] <= 2.0

    @pytest.mark.asyncio
    async def test_1113_insufficient_balance_fails_fast(self, monkeypatch):
        """A zero-balance 429 must propagate on attempt 1 with no backoff spent.

        It can never succeed, so the ~7s the rate-limit path is allowed to spend
        would be pure latency added to a guaranteed failure.
        """
        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("routes.chat.asyncio.sleep", fake_sleep)
        attempts = []

        async def factory_body():
            attempts.append(1)
            raise RuntimeError(
                "Error code: 429 - {'error': {'code': '1113', "
                "'message': 'Insufficient balance or no resource package'}}"
            )

        with pytest.raises(RuntimeError, match="1113"):
            await _run_with_llm_retry(lambda: factory_body())

        assert len(attempts) == 1
        assert sleeps == []

    @pytest.mark.asyncio
    async def test_default_max_attempts_is_four(self, monkeypatch):
        """Four attempts let a request survive three separate contention windows."""
        async def _no_sleep(delay):
            return None

        monkeypatch.setattr("routes.chat.asyncio.sleep", _no_sleep)
        attempts = []

        async def factory_body():
            attempts.append(1)
            raise RuntimeError("Error code: 429 - Rate limit reached for requests")

        with pytest.raises(RuntimeError, match="429"):
            await _run_with_llm_retry(lambda: factory_body())
        assert len(attempts) == 4

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
    async def test_backoff_doubles_with_equal_jitter(self, monkeypatch):
        """Exponential backoff over 1s, 2s, 4s windows, each equal-jittered to [0.5x, 1x].

        The jitter keeps requests rejected by one contention burst from waking in
        lockstep and colliding again — the free GLM tier serves ~3 concurrent.
        """
        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("routes.chat.asyncio.sleep", fake_sleep)

        async def always_transient():
            raise RuntimeError("Invalid response from upstream")

        with pytest.raises(RuntimeError):
            await _run_with_llm_retry(lambda: always_transient(), max_attempts=4, base_delay=1.0)

        # 3 sleeps between 4 attempts, each within its jittered window.
        assert len(sleeps) == 3
        assert 0.5 <= sleeps[0] <= 1.0
        assert 1.0 <= sleeps[1] <= 2.0
        assert 2.0 <= sleeps[2] <= 4.0

    @pytest.mark.asyncio
    async def test_backoff_delays_are_not_identical_across_runs(self, monkeypatch):
        """Jitter must actually vary — a fixed delay would re-collide in lockstep."""
        observed: list[float] = []

        async def fake_sleep(delay):
            observed.append(delay)

        monkeypatch.setattr("routes.chat.asyncio.sleep", fake_sleep)

        async def always_transient():
            raise RuntimeError("Invalid response from upstream")

        for _ in range(8):
            with pytest.raises(RuntimeError):
                await _run_with_llm_retry(
                    lambda: always_transient(), max_attempts=2, base_delay=1.0
                )

        assert len(set(observed)) > 1

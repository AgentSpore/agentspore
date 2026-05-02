"""Tests for providers.py and updated llm_fallback.py multi-provider chain.

Coverage:
  - Provider.is_active based on env var presence
  - Provider.api_key redaction (never logged — just ensure it returns env value)
  - parse_chain_entry: explicit provider prefix, legacy bare model ID, NVIDIA NIM
  - build_default_chain: respects active providers; skips inactive
  - _load_provider_chain: env override and default fallback
  - call_with_fallback: first-success, first-fail → next provider, all-fail
  - call_with_fallback: inactive provider skipped
  - call_with_fallback: non-retryable error stops chain immediately
  - LLMHealthChecker.check_model: ok, timeout, skipped (no key), unknown provider
  - LLMHealthChecker.check_all: returns one entry per chain slot
  - resolve_model_for_agent: requested in chain / not in chain / empty
  - FallbackError: message includes provider+model pairs
"""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from providers import (  # noqa: E402
    CEREBRAS,
    GROQ,
    NVIDIA_NIM,
    OPENROUTER,
    TOGETHER,
    Provider,
    ModelSpec,
    build_default_chain,
    parse_chain_entry,
    PROVIDER_BY_NAME,
    active_providers,
)
from llm_fallback import (  # noqa: E402
    FallbackError,
    LLMHealthChecker,
    call_with_fallback,
    resolve_model_for_agent,
    _load_provider_chain,
    DEFAULT_FALLBACK_CHAIN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


OK_BODY = {
    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
}

RATE_LIMIT_BODY: dict = {}  # body doesn't matter for HTTP 429

ERROR_200_BODY = {
    "error": {"code": 429, "message": "rate limit exceeded"},
}

NON_RETRYABLE_BODY = {
    "error": {"code": 404, "message": "no endpoints"},
}


# ---------------------------------------------------------------------------
# Provider / ModelSpec
# ---------------------------------------------------------------------------

class TestProviderIsActive:
    def test_active_when_env_set(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "nvkey123")
        provider = Provider(name="nvidia", base_url="https://x.y/v1", api_key_env="NVIDIA_API_KEY")
        assert provider.is_active is True

    def test_inactive_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        provider = Provider(name="nvidia", base_url="https://x.y/v1", api_key_env="NVIDIA_API_KEY")
        assert provider.is_active is False

    def test_inactive_when_env_empty(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "")
        provider = Provider(name="nvidia", base_url="https://x.y/v1", api_key_env="NVIDIA_API_KEY")
        assert provider.is_active is False

    def test_api_key_returns_env_value(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        assert GROQ.api_key == "gsk_test"

    def test_api_key_empty_when_not_set(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        assert GROQ.api_key == ""


class TestModelSpec:
    def test_defaults(self):
        m = ModelSpec(model_id="foo/bar")
        assert m.tool_use is True
        assert m.context_window == 128_000
        assert m.priority == 10

    def test_get_model_found(self):
        spec = NVIDIA_NIM.get_model("nvidia/llama-3.1-nemotron-70b-instruct")
        assert spec is not None
        assert spec.tool_use is True

    def test_get_model_not_found(self):
        assert NVIDIA_NIM.get_model("nonexistent/model") is None


# ---------------------------------------------------------------------------
# parse_chain_entry
# ---------------------------------------------------------------------------

class TestParseChainEntry:
    def test_explicit_provider_prefix(self):
        provider, model = parse_chain_entry("nvidia:nvidia/llama-3.1-nemotron-70b-instruct")
        assert provider == "nvidia"
        assert model == "nvidia/llama-3.1-nemotron-70b-instruct"

    def test_openrouter_explicit(self):
        provider, model = parse_chain_entry("openrouter:nvidia/nemotron-3-super-120b-a12b:free")
        assert provider == "openrouter"
        assert model == "nvidia/nemotron-3-super-120b-a12b:free"

    def test_legacy_bare_model_openrouter(self):
        """Bare model IDs without a known provider prefix → openrouter."""
        provider, model = parse_chain_entry("nvidia/nemotron-3-super-120b-a12b:free")
        assert provider == "openrouter"
        assert model == "nvidia/nemotron-3-super-120b-a12b:free"

    def test_groq_prefix(self):
        provider, model = parse_chain_entry("groq:llama-3.3-70b-versatile")
        assert provider == "groq"
        assert model == "llama-3.3-70b-versatile"

    def test_cerebras_prefix(self):
        provider, model = parse_chain_entry("cerebras:llama3.1-70b")
        assert provider == "cerebras"
        assert model == "llama3.1-70b"

    def test_together_prefix(self):
        provider, model = parse_chain_entry("together:meta-llama/Llama-3.3-70B-Instruct-Turbo")
        assert provider == "together"
        assert model == "meta-llama/Llama-3.3-70B-Instruct-Turbo"

    def test_unknown_prefix_falls_back_to_openrouter(self):
        """An unknown prefix that doesn't match any provider name → openrouter."""
        provider, model = parse_chain_entry("unknown:some/model")
        assert provider == "openrouter"
        assert model == "unknown:some/model"


# ---------------------------------------------------------------------------
# build_default_chain
# ---------------------------------------------------------------------------

class TestBuildDefaultChain:
    def test_always_includes_openrouter(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "or_key")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        chain = build_default_chain()
        providers_in_chain = {p for p, _ in chain}
        assert "openrouter" in providers_in_chain

    def test_nvidia_included_when_key_set(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "nvkey")
        chain = build_default_chain()
        providers_in_chain = {p for p, _ in chain}
        assert "nvidia" in providers_in_chain

    def test_nvidia_excluded_when_key_missing(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        chain = build_default_chain()
        providers_in_chain = {p for p, _ in chain}
        assert "nvidia" not in providers_in_chain

    def test_groq_included_when_key_set(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "groqkey")
        chain = build_default_chain()
        providers_in_chain = {p for p, _ in chain}
        assert "groq" in providers_in_chain

    def test_nvidia_entries_appear_before_openrouter(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "nvkey")
        chain = build_default_chain()
        providers_seq = [p for p, _ in chain]
        first_or = providers_seq.index("openrouter")
        first_nv = providers_seq.index("nvidia")
        assert first_nv < first_or, "NVIDIA should appear before OpenRouter"

    def test_embedding_model_excluded(self, monkeypatch):
        """NVIDIA embedding model (priority 99) must not appear in chain."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvkey")
        chain = build_default_chain()
        model_ids = {m for _, m in chain}
        assert "nvidia/llama-3.2-nv-embedqa-1b-v2" not in model_ids

    def test_chain_is_nonempty(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        chain = build_default_chain()
        assert len(chain) > 0


# ---------------------------------------------------------------------------
# _load_provider_chain (env override)
# ---------------------------------------------------------------------------

class TestLoadProviderChain:
    def test_env_override_parsed(self, monkeypatch):
        monkeypatch.setenv(
            "LLM_FALLBACK_CHAIN",
            "nvidia:nvidia/llama-3.1-nemotron-70b-instruct,openrouter:nvidia/nemotron-3-super-120b-a12b:free",
        )
        chain = _load_provider_chain()
        assert chain[0] == ("nvidia", "nvidia/llama-3.1-nemotron-70b-instruct")
        assert chain[1] == ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free")

    def test_empty_env_uses_default(self, monkeypatch):
        monkeypatch.setenv("LLM_FALLBACK_CHAIN", "")
        chain = _load_provider_chain()
        assert len(chain) > 0

    def test_missing_env_uses_default(self, monkeypatch):
        monkeypatch.delenv("LLM_FALLBACK_CHAIN", raising=False)
        chain = _load_provider_chain()
        assert len(chain) > 0

    def test_legacy_bare_model_ids_become_openrouter(self, monkeypatch):
        monkeypatch.setenv(
            "LLM_FALLBACK_CHAIN",
            "nvidia/nemotron-3-super-120b-a12b:free,openai/gpt-oss-120b:free",
        )
        chain = _load_provider_chain()
        assert chain[0] == ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free")
        assert chain[1] == ("openrouter", "openai/gpt-oss-120b:free")


# ---------------------------------------------------------------------------
# call_with_fallback
# ---------------------------------------------------------------------------

def _make_async_client(responses: list[Any]):
    """Build a mock httpx.AsyncClient whose post() returns responses in order."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(side_effect=responses)
    return client


class TestCallWithFallback:
    @pytest.mark.asyncio
    async def test_first_provider_success(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.delenv("LLM_FALLBACK_CHAIN", raising=False)

        ok_resp = _mock_response(200, OK_BODY)

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(return_value=ok_resp)
            MockClient.return_value = client_instance

            result = await call_with_fallback([{"role": "user", "content": "hi"}])

        assert result == OK_BODY

    @pytest.mark.asyncio
    async def test_first_fails_429_falls_to_next(self, monkeypatch):
        """First model returns 429, second succeeds."""
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        # Pin to two-model OpenRouter chain
        monkeypatch.setenv(
            "LLM_FALLBACK_CHAIN",
            "openrouter:model-a,openrouter:model-b",
        )

        rate_limit_resp = _mock_response(429, RATE_LIMIT_BODY)
        ok_resp = _mock_response(200, OK_BODY)

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(side_effect=[rate_limit_resp, ok_resp])
            MockClient.return_value = client_instance

            result = await call_with_fallback([{"role": "user", "content": "hi"}])

        assert result == OK_BODY

    @pytest.mark.asyncio
    async def test_all_fail_raises_fallback_error(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.setenv(
            "LLM_FALLBACK_CHAIN",
            "openrouter:model-a,openrouter:model-b",
        )

        rate_limit_resp = _mock_response(429, RATE_LIMIT_BODY)

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(return_value=rate_limit_resp)
            MockClient.return_value = client_instance

            with pytest.raises(FallbackError) as exc_info:
                await call_with_fallback([{"role": "user", "content": "hi"}])

        err = exc_info.value
        assert len(err.attempts) == 2
        assert "openrouter" in str(err)

    @pytest.mark.asyncio
    async def test_inactive_provider_skipped(self, monkeypatch):
        """NVIDIA provider skipped when NVIDIA_API_KEY not set."""
        monkeypatch.setenv("OPENAI_API_KEY", "orkey")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.setenv(
            "LLM_FALLBACK_CHAIN",
            "nvidia:nvidia/llama-3.1-nemotron-70b-instruct,openrouter:model-b",
        )

        ok_resp = _mock_response(200, OK_BODY)

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(return_value=ok_resp)
            MockClient.return_value = client_instance

            result = await call_with_fallback([{"role": "user", "content": "hi"}])

        # post() was called once (for openrouter), not twice
        assert client_instance.post.call_count == 1
        assert result == OK_BODY

    @pytest.mark.asyncio
    async def test_non_retryable_error_stops_chain(self, monkeypatch):
        """HTTP 200 with error.code=404 is non-retryable — stops immediately."""
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.setenv(
            "LLM_FALLBACK_CHAIN",
            "openrouter:model-a,openrouter:model-b",
        )

        bad_resp = _mock_response(200, NON_RETRYABLE_BODY)

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(return_value=bad_resp)
            MockClient.return_value = client_instance

            with pytest.raises(FallbackError) as exc_info:
                await call_with_fallback([{"role": "user", "content": "hi"}])

        # Only one attempt — chain stopped on non-retryable
        assert client_instance.post.call_count == 1
        assert len(exc_info.value.attempts) == 1

    @pytest.mark.asyncio
    async def test_timeout_triggers_next(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.setenv(
            "LLM_FALLBACK_CHAIN",
            "openrouter:model-a,openrouter:model-b",
        )
        ok_resp = _mock_response(200, OK_BODY)

        import httpx as _httpx

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(side_effect=[_httpx.TimeoutException("timed out"), ok_resp])
            MockClient.return_value = client_instance

            result = await call_with_fallback([{"role": "user", "content": "hi"}])

        assert result == OK_BODY

    @pytest.mark.asyncio
    async def test_200_error_body_retryable_code_falls_to_next(self, monkeypatch):
        """HTTP 200 with error.code=429 (OpenRouter style) → retry next."""
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.setenv(
            "LLM_FALLBACK_CHAIN",
            "openrouter:model-a,openrouter:model-b",
        )

        error_resp = _mock_response(200, ERROR_200_BODY)
        ok_resp = _mock_response(200, OK_BODY)

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(side_effect=[error_resp, ok_resp])
            MockClient.return_value = client_instance

            result = await call_with_fallback([{"role": "user", "content": "hi"}])

        assert result == OK_BODY

    @pytest.mark.asyncio
    async def test_extra_body_merged(self, monkeypatch):
        """extra_body fields appear in the request body."""
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.setenv("LLM_FALLBACK_CHAIN", "openrouter:model-a")

        ok_resp = _mock_response(200, OK_BODY)

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(return_value=ok_resp)
            MockClient.return_value = client_instance

            await call_with_fallback(
                [{"role": "user", "content": "hi"}],
                extra_body={"temperature": 0.5},
            )

        call_kwargs = client_instance.post.call_args
        body_sent = call_kwargs.kwargs.get("json") or call_kwargs.args[1]
        assert body_sent.get("temperature") == 0.5


# ---------------------------------------------------------------------------
# FallbackError
# ---------------------------------------------------------------------------

class TestFallbackError:
    def test_message_includes_provider_and_model(self):
        attempts = [
            {"provider": "nvidia", "model": "nvidia/llama-3.1", "error": "timeout", "latency_ms": 5000},
            {"provider": "openrouter", "model": "nvidia/nemotron", "error": "HTTP 429", "latency_ms": 300},
        ]
        err = FallbackError(attempts)
        msg = str(err)
        assert "nvidia:nvidia/llama-3.1" in msg
        assert "openrouter:nvidia/nemotron" in msg
        assert err.attempts is attempts


# ---------------------------------------------------------------------------
# LLMHealthChecker
# ---------------------------------------------------------------------------

class TestLLMHealthChecker:
    @pytest.mark.asyncio
    async def test_ok_response(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        checker = LLMHealthChecker(timeout=5.0)
        ok_resp = _mock_response(200, OK_BODY)

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(return_value=ok_resp)
            MockClient.return_value = client_instance

            result = await checker.check_model("openrouter", "nvidia/nemotron-3-super-120b-a12b:free")

        assert result["status"] == "ok"
        assert result["provider"] == "openrouter"
        assert result["model"] == "nvidia/nemotron-3-super-120b-a12b:free"
        assert result["error"] is None
        assert isinstance(result["latency_ms"], int)

    @pytest.mark.asyncio
    async def test_timeout_response(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        import httpx as _httpx
        checker = LLMHealthChecker(timeout=5.0)

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(side_effect=_httpx.TimeoutException("timed out"))
            MockClient.return_value = client_instance

            result = await checker.check_model("openrouter", "some/model")

        assert result["status"] == "timeout"
        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_skipped_when_no_key(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        checker = LLMHealthChecker(timeout=5.0)
        result = await checker.check_model("nvidia", "nvidia/llama-3.1-nemotron-70b-instruct")
        assert result["status"] == "skipped"
        assert "NVIDIA_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_provider(self, monkeypatch):
        checker = LLMHealthChecker(timeout=5.0)
        result = await checker.check_model("phantom", "some/model")
        assert result["status"] == "error"
        assert "Unknown provider" in result["error"]

    @pytest.mark.asyncio
    async def test_error_body_400(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        checker = LLMHealthChecker(timeout=5.0)
        err_resp = _mock_response(400, {"error": {"message": "bad request"}})

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(return_value=err_resp)
            MockClient.return_value = client_instance

            result = await checker.check_model("openrouter", "some/model")

        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_check_all_returns_one_entry_per_chain(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.setenv(
            "LLM_FALLBACK_CHAIN",
            "openrouter:model-a,openrouter:model-b",
        )
        checker = LLMHealthChecker(timeout=5.0)
        ok_resp = _mock_response(200, OK_BODY)

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(return_value=ok_resp)
            MockClient.return_value = client_instance

            results = await checker.check_all()

        assert len(results) == 2
        assert all(r["status"] == "ok" for r in results)

    @pytest.mark.asyncio
    async def test_check_all_includes_skipped_for_inactive(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "key")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.setenv(
            "LLM_FALLBACK_CHAIN",
            "nvidia:nvidia/llama-3.1-nemotron-70b-instruct,openrouter:model-b",
        )
        checker = LLMHealthChecker(timeout=5.0)
        ok_resp = _mock_response(200, OK_BODY)

        with patch("llm_fallback.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            client_instance.post = AsyncMock(return_value=ok_resp)
            MockClient.return_value = client_instance

            results = await checker.check_all()

        assert len(results) == 2
        statuses = {(r["provider"], r["status"]) for r in results}
        assert ("nvidia", "skipped") in statuses
        assert ("openrouter", "ok") in statuses


# ---------------------------------------------------------------------------
# resolve_model_for_agent (backward compat)
# ---------------------------------------------------------------------------

class TestResolveModelForAgent:
    def test_returns_requested_when_in_chain(self, monkeypatch):
        monkeypatch.delenv("LLM_FALLBACK_CHAIN", raising=False)
        model = DEFAULT_FALLBACK_CHAIN[1]
        assert resolve_model_for_agent(model) == model

    def test_returns_chain_head_when_not_in_chain(self, monkeypatch):
        monkeypatch.delenv("LLM_FALLBACK_CHAIN", raising=False)
        result = resolve_model_for_agent("nonexistent/model")
        assert result == DEFAULT_FALLBACK_CHAIN[0]

    def test_returns_chain_head_when_empty_string(self, monkeypatch):
        monkeypatch.delenv("LLM_FALLBACK_CHAIN", raising=False)
        result = resolve_model_for_agent("")
        assert result == DEFAULT_FALLBACK_CHAIN[0]

    def test_custom_chain_env(self, monkeypatch):
        monkeypatch.setenv(
            "LLM_FALLBACK_CHAIN",
            "custom/model-a,custom/model-b",
        )
        result = resolve_model_for_agent("custom/model-a")
        assert result == "custom/model-a"

    def test_custom_chain_env_with_provider_prefix_extracts_model(self, monkeypatch):
        """Provider-prefixed entries: model_id is extracted for flat model list."""
        monkeypatch.setenv(
            "LLM_FALLBACK_CHAIN",
            "nvidia:nvidia/llama-3.1-nemotron-70b-instruct",
        )
        result = resolve_model_for_agent("nvidia/llama-3.1-nemotron-70b-instruct")
        assert result == "nvidia/llama-3.1-nemotron-70b-instruct"


# ---------------------------------------------------------------------------
# active_providers helper
# ---------------------------------------------------------------------------

class TestActiveProviders:
    def test_only_active_providers_returned(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "or_key")
        monkeypatch.setenv("NVIDIA_API_KEY", "nv_key")
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
        monkeypatch.delenv("TOGETHER_API_KEY", raising=False)

        active = active_providers()
        names = {p.name for p in active}
        assert "openrouter" in names
        assert "nvidia" in names
        assert "groq" not in names
        assert "cerebras" not in names
        assert "together" not in names

    def test_empty_when_no_keys(self, monkeypatch):
        for env in ["OPENAI_API_KEY", "NVIDIA_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "TOGETHER_API_KEY"]:
            monkeypatch.delenv(env, raising=False)
        assert active_providers() == []

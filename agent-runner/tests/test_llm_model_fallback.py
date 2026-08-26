"""Tests for _build_fallback_models / _run_with_model_fallback in routes/chat.py.

Covers the model-level fallback that switches to the next entry in
LLM_FALLBACK_CHAIN on a transient error, instead of only retrying the same
dead/rate-limited model.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from pydantic_ai.providers.openai import OpenAIProvider

from routes.chat import _build_fallback_models, _run_with_model_fallback

_TEST_PROVIDER = OpenAIProvider(base_url="https://example.invalid/v1", api_key="test-key")


def _session(model: str, provider=_TEST_PROVIDER):
    session = MagicMock()
    session.model = model
    session.openai_provider = provider
    return session


class TestBuildFallbackModels:
    def test_first_entry_is_current_model_with_none(self, monkeypatch):
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["a/model-1", "b/model-2"])
        session = _session("current-model")
        models = _build_fallback_models(session)
        assert models[0] == ("current-model", None)

    def test_chain_entry_matching_current_model_is_skipped(self, monkeypatch):
        """A chain entry equal to session.model must not repeat the failing model."""
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["current-model", "b/model-2"])
        session = _session("current-model")
        models = _build_fallback_models(session)
        labels = [label for label, _ in models]
        assert labels == ["current-model", "b/model-2"]

    def test_no_provider_returns_only_current_model(self, monkeypatch):
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["a/model-1"])
        session = _session("current-model", provider=None)
        models = _build_fallback_models(session)
        assert models == [("current-model", None)]

    def test_remaining_entries_carry_model_objects_not_strings(self, monkeypatch):
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["a/model-1"])
        session = _session("current-model")
        models = _build_fallback_models(session)
        _, second_model = models[1]
        assert second_model is not None
        assert not isinstance(second_model, str)


class TestRunWithModelFallback:
    @pytest.mark.asyncio
    async def test_falls_over_to_second_model_on_transient_error(self, monkeypatch):
        """Current model exhausts its retries; the call that finally succeeds must
        have received a DIFFERENT model object (the second chain entry), not a repeat
        of the first model's `None` (agent-default) marker."""
        monkeypatch.setattr("routes.chat.asyncio.sleep", lambda *_: _noop())
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["b/model-2"])
        session = _session("current-model")

        models_used = []

        async def run_factory(model, timeout):
            models_used.append(model)
            if model is None:
                raise RuntimeError("503 Service Unavailable")
            return "ok"

        result = await _run_with_model_fallback(
            session, run_factory, deadline=time.monotonic() + 30,
        )
        assert result == "ok"
        assert all(m is None for m in models_used[:-1])  # current model retried, all failed
        assert models_used[-1] is not None  # final, successful call used the fallback model

    @pytest.mark.asyncio
    async def test_current_model_chain_entry_is_skipped(self, monkeypatch):
        """Chain entry equal to session.model must not be retried as attempt #2."""
        monkeypatch.setattr("routes.chat.asyncio.sleep", lambda *_: _noop())
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["current-model", "c/model-3"])
        session = _session("current-model")

        models_used = []

        async def run_factory(model, timeout):
            models_used.append(model)
            if len(models_used) == 1:
                raise RuntimeError("503 Service Unavailable")
            return "ok"

        result = await _run_with_model_fallback(
            session, run_factory, deadline=time.monotonic() + 30,
        )
        assert result == "ok"
        assert len(models_used) == 2  # not 3 — "current-model" chain entry was skipped

    @pytest.mark.asyncio
    async def test_all_models_fail_raises_last_exception(self, monkeypatch):
        monkeypatch.setattr("routes.chat.asyncio.sleep", lambda *_: _noop())
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["b/model-2"])
        session = _session("current-model")

        async def run_factory(model, timeout):
            raise RuntimeError("503 Service Unavailable")

        with pytest.raises(RuntimeError, match="503"):
            await _run_with_model_fallback(session, run_factory, deadline=time.monotonic() + 30)

    @pytest.mark.asyncio
    async def test_non_transient_error_does_not_try_next_model(self, monkeypatch):
        """A 401 config error must fail on the first model, no fallback attempted."""
        monkeypatch.setattr("routes.chat.asyncio.sleep", lambda *_: _noop())
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["b/model-2"])
        session = _session("current-model")

        models_used = []

        async def run_factory(model, timeout):
            models_used.append(model)
            raise RuntimeError("Error code: 401 - invalid api key")

        with pytest.raises(RuntimeError, match="401"):
            await _run_with_model_fallback(session, run_factory, deadline=time.monotonic() + 30)
        assert len(models_used) == 1

    @pytest.mark.asyncio
    async def test_deadline_bounds_the_whole_chain(self, monkeypatch):
        """An already-expired deadline must stop the chain before trying any model."""
        monkeypatch.setattr("routes.chat.asyncio.sleep", lambda *_: _noop())
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["b/model-2", "c/model-3"])
        session = _session("current-model")

        models_used = []

        async def run_factory(model, timeout):
            models_used.append(model)
            raise RuntimeError("503 Service Unavailable")

        with pytest.raises(RuntimeError, match="503|exhausted"):
            await _run_with_model_fallback(session, run_factory, deadline=time.monotonic() - 1)
        assert len(models_used) == 0

    @pytest.mark.asyncio
    async def test_deadline_expiring_mid_chain_stops_the_remaining_models(self, monkeypatch):
        """The deadline must be re-checked between models, not only at chain entry —
        a deadline that expires after the first model's retries must stop the chain
        instead of moving on to the second model."""
        monkeypatch.setattr("routes.chat.asyncio.sleep", lambda *_: _noop())
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["b/model-2", "c/model-3"])
        session = _session("current-model")

        models_used = []
        deadline = time.monotonic() + 0.05

        async def run_factory(model, timeout):
            models_used.append(model)
            time.sleep(0.06)  # pushes monotonic() past `deadline` before the 2nd model
            raise RuntimeError("503 Service Unavailable")

        with pytest.raises(RuntimeError, match="503|exhausted"):
            await _run_with_model_fallback(session, run_factory, deadline=deadline)
        assert len(models_used) == 1  # only the first model was attempted


async def _noop(*_args, **_kwargs):
    return None

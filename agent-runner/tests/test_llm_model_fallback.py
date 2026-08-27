"""Tests for _build_fallback_models / _run_with_model_fallback in routes/chat.py.

Covers the model-level fallback that switches to the next entry in
LLM_FALLBACK_CHAIN on a transient error, instead of only retrying the same
dead/rate-limited model.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import DeferredToolRequests
from pydantic_ai.providers.openai import OpenAIProvider

from routes.chat import _build_fallback_models, _run_chat_nonstream, _run_with_model_fallback
from schemas import ChatRequest

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

        result, winning_model = await _run_with_model_fallback(
            session, run_factory, deadline=time.monotonic() + 30,
        )
        assert result == "ok"
        assert all(m is None for m in models_used[:-1])  # current model retried, all failed
        assert models_used[-1] is not None  # final, successful call used the fallback model
        assert winning_model is models_used[-1]

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

        result, _winning_model = await _run_with_model_fallback(
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


class TestPinnedModel:
    @pytest.mark.asyncio
    async def test_pinned_model_is_tried_first_not_the_agent_default(self, monkeypatch):
        """Reproduces the battle scenario: model fails, fallback succeeds on
        model B, and the next call (deferred-tool approval loop) must go
        straight to model B instead of re-trying the agent's own dead model."""
        monkeypatch.setattr("routes.chat.asyncio.sleep", lambda *_: _noop())
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["b/model-2", "c/model-3"])
        session = _session("current-model")
        _, model_b = _build_fallback_models(session)[1]

        models_used = []

        async def run_factory(model, timeout):
            models_used.append(model)
            return "ok"

        result, winning_model = await _run_with_model_fallback(
            session, run_factory, deadline=time.monotonic() + 30, pinned_model=model_b,
        )
        assert result == "ok"
        assert models_used == [model_b]  # went straight to B, never re-tried None (agent default)
        assert winning_model is model_b

    @pytest.mark.asyncio
    async def test_pinned_model_failing_falls_through_to_remaining_chain(self, monkeypatch):
        """If the pinned model has died since, the chain must continue past it
        instead of getting stuck — the pinned model isn't retried a second time
        once it fails."""
        monkeypatch.setattr("routes.chat.asyncio.sleep", lambda *_: _noop())
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["b/model-2", "c/model-3"])
        session = _session("current-model")
        _, model_b = _build_fallback_models(session)[1]

        # OpenAIModel objects are rebuilt fresh on every _build_fallback_models
        # call, so identity isn't stable across calls — assert on model_name.
        def name(m):
            return None if m is None else m.model_name

        models_used = []

        async def run_factory(model, timeout):
            models_used.append(name(model))
            if name(model) == "model-3":  # _api_model_id strips the "c/" provider prefix
                return "ok"
            raise RuntimeError("503 Service Unavailable")

        result, winning_model = await _run_with_model_fallback(
            session, run_factory, deadline=time.monotonic() + 30, pinned_model=model_b,
        )
        assert result == "ok"
        # pinned model gets its own _run_with_llm_retry cycle (2 attempts) but is not
        # re-entered as a separate chain slot afterwards — no duplicate slot for it.
        assert models_used.count("model-2") == 2
        assert name(winning_model) == "model-3"


def _fake_result(output="ok"):
    result = MagicMock()
    result.output = output
    result.all_messages.return_value = []
    result.new_messages.return_value = []
    return result


class TestNonstreamApprovalLoopModelPin:
    @pytest.mark.asyncio
    async def test_approval_loop_stays_on_the_model_that_just_won(self, monkeypatch):
        """Reproduces the battle scenario: the agent's own model dies, fallback
        succeeds on model B, and the deferred-tool auto-approval re-run must go
        to model B — not back to the agent's dead default model."""
        monkeypatch.setattr("routes.chat.asyncio.sleep", lambda *_: _noop())
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: ["b/model-2"])
        monkeypatch.setattr("routes.chat.sanitize_history", lambda messages: messages)
        monkeypatch.setattr(
            "routes.chat._extract_response", lambda result: ("done", [], None),
        )

        session = _session("current-model")
        session.chat_lock = AsyncMock()
        session.agent_handle = None

        deferred = MagicMock(spec=DeferredToolRequests)
        deferred.approvals = []
        first_result = _fake_result(output=deferred)
        second_result = _fake_result(output="done")

        models_used = []

        async def agent_run(*_args, model=None, **_kwargs):
            name = None if model is None else model.model_name
            models_used.append(name)
            if "deferred_tool_results" not in _kwargs:
                if model is None:
                    raise RuntimeError("400 model_unavailable")
                return first_result
            # Approval re-run: must be called with the winning model (model-2),
            # never with None (the agent's own dead default).
            assert model is not None, "approval loop fell back to the agent's dead default model"
            return second_result

        session.agent = MagicMock()
        session.agent.run = AsyncMock(side_effect=agent_run)

        body = ChatRequest(content="hi")
        await _run_chat_nonstream("agent-1", body, session, [])

        assert all(m is None for m in models_used[:-2])  # agent default retried 4x, all failed
        assert models_used[-2] == "model-2"  # fallback won the first turn
        assert models_used[-1] == "model-2"  # approval re-run pinned to the winner

    @pytest.mark.asyncio
    async def test_approval_loop_reuses_the_same_deadline(self, monkeypatch):
        """The deadline computed at the top of _run_chat_nonstream must be passed
        through unchanged to the approval loop's fallback call — a fresh deadline
        per iteration would make settings.chat_timeout unbounded across up to 10
        approval iterations."""
        monkeypatch.setattr("routes.chat.asyncio.sleep", lambda *_: _noop())
        monkeypatch.setattr("routes.chat._load_model_chain", lambda: [])
        monkeypatch.setattr("routes.chat.sanitize_history", lambda messages: messages)
        monkeypatch.setattr(
            "routes.chat._extract_response", lambda result: ("done", [], None),
        )

        session = _session("current-model")
        session.chat_lock = AsyncMock()
        session.agent_handle = None

        deferred = MagicMock(spec=DeferredToolRequests)
        deferred.approvals = []
        first_result = _fake_result(output=deferred)
        second_result = _fake_result(output="done")

        deadlines_seen = []

        async def agent_run(*_args, model=None, model_settings=None, **_kwargs):
            deadlines_seen.append(model_settings["timeout"])
            if "deferred_tool_results" not in _kwargs:
                return first_result
            return second_result

        session.agent = MagicMock()
        session.agent.run = AsyncMock(side_effect=agent_run)

        body = ChatRequest(content="hi")
        await _run_chat_nonstream("agent-1", body, session, [])

        # Both calls share one deadline: the 2nd timeout must be <= the 1st, and
        # not a freshly re-maxed settings.chat_timeout.
        assert deadlines_seen[1] <= deadlines_seen[0]


async def _noop(*_args, **_kwargs):
    return None

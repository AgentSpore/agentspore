"""Unit tests for BaggageSpanProcessor and use_agent_context baggage propagation.

Tests verify:
- BaggageSpanProcessor copies baggage keys onto span attributes at on_start
- use_agent_context sets OTel baggage so BaggageSpanProcessor can propagate
  them to child spans
"""

from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry import baggage, context


class TestBaggageSpanProcessor:
    def _make_processor(self):
        from observability import BaggageSpanProcessor

        return BaggageSpanProcessor()

    def _make_span(self) -> MagicMock:
        span = MagicMock()
        span.set_attribute = MagicMock()
        return span

    def test_copies_baggage_keys_onto_span(self):
        """on_start must set span attributes for each baggage key that is present."""
        processor = self._make_processor()
        span = self._make_span()

        ctx = context.get_current()
        ctx = baggage.set_baggage("agent_id", "aid-1", ctx)
        ctx = baggage.set_baggage("agent_handle", "mybot", ctx)
        ctx = baggage.set_baggage("model", "gpt-4o", ctx)

        processor.on_start(span, parent_context=ctx)

        calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
        assert calls["agent_id"] == "aid-1"
        assert calls["agent_handle"] == "mybot"
        assert calls["model"] == "gpt-4o"

    def test_missing_baggage_keys_not_set(self):
        """on_start must not set attributes for keys absent from baggage."""
        processor = self._make_processor()
        span = self._make_span()

        ctx = context.get_current()
        ctx = baggage.set_baggage("agent_id", "x", ctx)
        # agent_handle, model, cron_run_id absent

        processor.on_start(span, parent_context=ctx)

        set_keys = {c.args[0] for c in span.set_attribute.call_args_list}
        assert "agent_id" in set_keys
        assert "agent_handle" not in set_keys
        assert "model" not in set_keys

    def test_uses_current_context_when_parent_none(self):
        """on_start with parent_context=None must fall back to context.get_current()."""
        processor = self._make_processor()
        span = self._make_span()

        ctx = context.get_current()
        ctx = baggage.set_baggage("cron_run_id", "run-99", ctx)
        token = context.attach(ctx)
        try:
            processor.on_start(span, parent_context=None)
        finally:
            context.detach(token)

        calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
        assert calls["cron_run_id"] == "run-99"

    def test_lifecycle_methods_do_not_raise(self):
        """on_end, shutdown, force_flush must be safe no-ops."""
        processor = self._make_processor()
        processor.on_end(MagicMock())
        processor.shutdown()
        assert processor.force_flush() is True
        assert processor.force_flush(timeout_millis=5000) is True


class TestUseAgentContextBaggagePropagation:
    @pytest.mark.asyncio
    async def test_baggage_set_inside_context(self):
        """use_agent_context must attach baggage to OTel context so child reads it."""
        with patch("logfire.span") as mock_span:
            ctx_mgr = MagicMock()
            ctx_mgr.__enter__ = MagicMock(return_value=ctx_mgr)
            ctx_mgr.__exit__ = MagicMock(return_value=False)
            mock_span.return_value = ctx_mgr

            from observability import use_agent_context

            observed: dict = {}

            async with use_agent_context(
                agent_id="a1", agent_handle="hbot", model="llm-x", cron_run_id="cr-7"
            ):
                observed["agent_id"] = baggage.get_baggage("agent_id")
                observed["agent_handle"] = baggage.get_baggage("agent_handle")
                observed["model"] = baggage.get_baggage("model")
                observed["cron_run_id"] = baggage.get_baggage("cron_run_id")

        assert observed["agent_id"] == "a1"
        assert observed["agent_handle"] == "hbot"
        assert observed["model"] == "llm-x"
        assert observed["cron_run_id"] == "cr-7"

    @pytest.mark.asyncio
    async def test_baggage_detached_after_context(self):
        """Baggage must not leak outside use_agent_context block."""
        with patch("logfire.span") as mock_span:
            ctx_mgr = MagicMock()
            ctx_mgr.__enter__ = MagicMock(return_value=ctx_mgr)
            ctx_mgr.__exit__ = MagicMock(return_value=False)
            mock_span.return_value = ctx_mgr

            from observability import use_agent_context

            async with use_agent_context(agent_id="leak-test", agent_handle="bot"):
                pass

        # After exit, baggage should not be set in current context
        assert baggage.get_baggage("agent_id") is None
        assert baggage.get_baggage("agent_handle") is None

    @pytest.mark.asyncio
    async def test_empty_attrs_yields_without_baggage(self):
        """use_agent_context with all-None args must yield without touching OTel."""
        with patch("logfire.span") as mock_span:
            from observability import use_agent_context

            async with use_agent_context():
                pass

            mock_span.assert_not_called()

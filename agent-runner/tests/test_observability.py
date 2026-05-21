"""Tests for agent-runner observability module.

Covers configure() no-op behaviour (OTEL_EXPORTER_OTLP_ENDPOINT unset) and
use_agent_context() span context manager — without requiring a real OTLP
endpoint or logfire account.
"""

import os
from unittest.mock import MagicMock, patch

import pytest


class TestConfigure:
    def test_noop_when_endpoint_unset(self):
        """configure() must not call logfire.configure when OTEL endpoint is absent."""
        env = {k: v for k, v in os.environ.items() if k != "OTEL_EXPORTER_OTLP_ENDPOINT"}
        with patch.dict(os.environ, env, clear=True):
            with patch("logfire.configure") as mock_cfg:
                from observability import configure

                configure()
                mock_cfg.assert_not_called()

    def test_configures_when_endpoint_set(self):
        """configure() calls logfire.configure when OTEL_EXPORTER_OTLP_ENDPOINT is set."""
        env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318"}
        with patch.dict(os.environ, env):
            with patch("logfire.configure") as mock_cfg, \
                 patch("logfire.instrument_pydantic_ai"), \
                 patch("logfire.instrument_httpx"), \
                 patch("logfire.instrument_asyncpg"):
                from observability import configure

                configure()
                mock_cfg.assert_called_once()
                call_kwargs = mock_cfg.call_args.kwargs
                assert call_kwargs.get("send_to_logfire") is False

    def test_instruments_fastapi_when_app_provided(self):
        """configure(app=...) calls instrument_fastapi with the given app."""
        fake_app = MagicMock()
        env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318"}
        with patch.dict(os.environ, env):
            with patch("logfire.configure"), \
                 patch("logfire.instrument_pydantic_ai"), \
                 patch("logfire.instrument_httpx"), \
                 patch("logfire.instrument_asyncpg"), \
                 patch("logfire.instrument_fastapi") as mock_fi:
                from observability import configure

                configure(app=fake_app)
                mock_fi.assert_called_once_with(fake_app, capture_headers=False)


class TestUseAgentContext:
    @pytest.mark.asyncio
    async def test_yields_inside_span(self):
        """use_agent_context() must yield control inside a logfire.span context."""
        ran = []
        with patch("logfire.span") as mock_span:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_span.return_value = ctx

            from observability import use_agent_context

            async with use_agent_context(agent_id="test-id", agent_handle="bot"):
                ran.append(True)

        assert ran == [True]
        mock_span.assert_called_once()
        call_kwargs = mock_span.call_args
        assert "agent_id" in call_kwargs.kwargs or "test-id" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_none_kwargs_excluded_from_span(self):
        """None kwargs must not appear as span attributes."""
        with patch("logfire.span") as mock_span:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_span.return_value = ctx

            from observability import use_agent_context

            async with use_agent_context(agent_id="abc", agent_handle=None, model=None):
                pass

        _, span_kwargs = mock_span.call_args
        assert "agent_handle" not in span_kwargs
        assert "model" not in span_kwargs
        assert span_kwargs["agent_id"] == "abc"

    @pytest.mark.asyncio
    async def test_all_kwargs_passed_to_span(self):
        """All non-None kwargs become span attributes."""
        with patch("logfire.span") as mock_span:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_span.return_value = ctx

            from observability import use_agent_context

            async with use_agent_context(
                agent_id="id1",
                agent_handle="mybot",
                model="gpt-4o",
                cron_run_id="task-99",
            ):
                pass

        _, span_kwargs = mock_span.call_args
        assert span_kwargs["agent_id"] == "id1"
        assert span_kwargs["agent_handle"] == "mybot"
        assert span_kwargs["model"] == "gpt-4o"
        assert span_kwargs["cron_run_id"] == "task-99"

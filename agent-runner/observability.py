"""Logfire OTLP instrumentation for agent-runner.

Auto-instruments FastAPI + pydantic-ai + asyncpg + httpx. Provides
`use_agent_context()` async context manager to set per-agent labels on the
root span. Child spans inherit via OTel W3C Baggage propagation so that
nested asyncpg/httpx/pydantic-ai spans carry agent_handle and model without
manual instrumentation inside business code.

Sends to local OTLP collector (Jaeger) only — send_to_logfire=False.
No-op when OTEL_EXPORTER_OTLP_ENDPOINT is unset (local dev, CI).

Each logfire.instrument_* call is wrapped in try/except so that a missing
optional dependency or version mismatch causes a logged warning rather than
a crash at startup.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import logfire
from loguru import logger
from opentelemetry import baggage, context
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace import SpanProcessor

_OPTIONAL_INSTRUMENTS = (
    "instrument_httpx",
    "instrument_asyncpg",
    "instrument_pydantic_ai",
)

_BAGGAGE_KEYS = ("agent_id", "agent_handle", "model", "cron_run_id")


class BaggageSpanProcessor(SpanProcessor):
    """Copy W3C Baggage keys onto every span at start as span attributes.

    Baggage propagates through OTel context across async task boundaries and
    HTTP request headers (W3C Baggage spec). This processor materialises
    baggage values as span attributes so they are queryable in Jaeger/Prometheus
    without manual instrumentation inside every span.
    """

    def on_start(self, span: ReadableSpan, parent_context=None) -> None:  # type: ignore[override]
        ctx = parent_context if parent_context is not None else context.get_current()
        for key in _BAGGAGE_KEYS:
            value = baggage.get_baggage(key, ctx)
            if value is not None:
                span.set_attribute(key, value)

    def on_end(self, span: ReadableSpan) -> None:  # type: ignore[override]
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def configure(app=None) -> None:
    """Configure logfire. No-op if OTEL_EXPORTER_OTLP_ENDPOINT unset.

    Sends to local OTLP collector (Jaeger) only — send_to_logfire=False.
    Auto-instruments pydantic-ai, httpx, asyncpg. Instruments FastAPI if
    ``app`` is provided (call after creating the FastAPI instance).

    Installs BaggageSpanProcessor so that baggage set in use_agent_context()
    propagates to all nested spans (asyncpg, httpx, pydantic-ai).

    Each instrument call degrades gracefully when the underlying package is
    absent or incompatible — startup is never blocked by an optional dep.
    """
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return

    logfire.configure(
        service_name=os.getenv("OTEL_SERVICE_NAME", "agent-runner"),
        service_version=os.getenv("APP_VERSION", "dev"),
        send_to_logfire=False,
    )

    # Install BaggageSpanProcessor before auto-instruments fire so that
    # all child spans produced by httpx/asyncpg/pydantic-ai get the attrs.
    from opentelemetry import trace as otel_trace

    tracer_provider = otel_trace.get_tracer_provider()
    if hasattr(tracer_provider, "add_span_processor"):
        tracer_provider.add_span_processor(BaggageSpanProcessor())

    for fn_name in _OPTIONAL_INSTRUMENTS:
        fn = getattr(logfire, fn_name, None)
        if fn is None:
            continue
        try:
            fn()
        except (ImportError, Exception) as exc:
            logger.info("logfire {} skipped: {}", fn_name, exc)

    if app is not None:
        try:
            logfire.instrument_fastapi(app, capture_headers=False)
        except Exception as exc:
            logger.info("logfire instrument_fastapi skipped: {}", exc)


@asynccontextmanager
async def use_agent_context(
    *,
    agent_id: str | None = None,
    agent_handle: str | None = None,
    model: str | None = None,
    cron_run_id: str | None = None,
) -> AsyncIterator[None]:
    """Root span for an agent operation with W3C Baggage propagation.

    Sets OTel baggage for all keys so that BaggageSpanProcessor materialises
    them as attributes on every child span (asyncpg queries, httpx calls,
    pydantic-ai runs). This replaces the previous approach where child spans
    had no agent_handle/model attributes.

    Use in chat handler / hosted-agent service entry points. FastAPI
    auto-instrumentation already covers the HTTP-level span; this adds
    per-agent attributes so Jaeger/Grafana can filter by agent_id or handle.

    All non-None kwargs become span attributes AND baggage entries.
    """
    attrs: dict[str, str] = {
        k: v
        for k, v in {
            "agent_id": agent_id,
            "agent_handle": agent_handle,
            "model": model,
            "cron_run_id": cron_run_id,
        }.items()
        if v is not None
    }

    if not attrs:
        yield
        return

    # Set W3C Baggage so child spans (asyncpg/httpx/pydantic-ai) inherit attrs.
    ctx = context.get_current()
    for k, v in attrs.items():
        ctx = baggage.set_baggage(k, str(v), ctx)
    token = context.attach(ctx)
    try:
        with logfire.span("agent.operation", **attrs):
            yield
    finally:
        context.detach(token)

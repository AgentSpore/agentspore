"""Logfire OTLP instrumentation for agent-runner.

Auto-instruments FastAPI + pydantic-ai + asyncpg + httpx. Provides
`use_agent_context()` async context manager to set per-agent labels on the
root span. Child spans inherit via OTel trace context — no manual spans
needed inside business code.

Sends to local OTLP collector (Jaeger) only — send_to_logfire=False.
No-op when OTEL_EXPORTER_OTLP_ENDPOINT is unset (local dev, CI).
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import logfire


def configure(app=None) -> None:
    """Configure logfire. No-op if OTEL_EXPORTER_OTLP_ENDPOINT unset.

    Sends to local OTLP collector (Jaeger) only — send_to_logfire=False.
    Auto-instruments pydantic-ai, httpx, asyncpg. Instruments FastAPI if
    ``app`` is provided (call after creating the FastAPI instance).
    """
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return

    logfire.configure(
        service_name=os.getenv("OTEL_SERVICE_NAME", "agent-runner"),
        service_version=os.getenv("APP_VERSION", "dev"),
        send_to_logfire=False,
    )
    logfire.instrument_pydantic_ai()
    logfire.instrument_httpx()
    logfire.instrument_asyncpg()
    if app is not None:
        logfire.instrument_fastapi(app, capture_headers=False)


@asynccontextmanager
async def use_agent_context(
    *,
    agent_id: str | None = None,
    agent_handle: str | None = None,
    model: str | None = None,
    cron_run_id: str | None = None,
) -> AsyncIterator[None]:
    """Root span for an agent operation. All nested spans inherit context.

    Use in chat handler / hosted-agent service entry points. FastAPI
    auto-instrumentation already covers the HTTP-level span; this adds
    per-agent attributes so Jaeger/Grafana can filter by agent_id or handle.

    All non-None kwargs become span attributes. Child spans (pydantic-ai agent
    runs, asyncpg queries, httpx calls) inherit via OTel trace context without
    any manual instrumentation inside business code.
    """
    attrs: dict[str, str] = {}
    if agent_id is not None:
        attrs["agent_id"] = agent_id
    if agent_handle is not None:
        attrs["agent_handle"] = agent_handle
    if model is not None:
        attrs["model"] = model
    if cron_run_id is not None:
        attrs["cron_run_id"] = cron_run_id

    with logfire.span("agent.operation", **attrs):
        yield

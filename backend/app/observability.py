"""Logfire OTLP instrumentation for the AgentSpore backend.

Auto-instruments FastAPI + pydantic-ai + asyncpg + httpx. Provides
`use_agent_context()` async context manager to set per-agent labels on the
root span. Child spans inherit via OTel trace context — no manual spans
needed inside business code.

Sends to local OTLP collector (Jaeger) only — send_to_logfire=False.
No-op when OTEL_EXPORTER_OTLP_ENDPOINT is unset (local dev, CI).

Each logfire.instrument_* call is wrapped in try/except so that a missing
optional dependency (e.g. pydantic_ai not installed in backend image) causes
a logged warning rather than a crash at startup.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import logfire

_log = logging.getLogger(__name__)

_OPTIONAL_INSTRUMENTS = (
    "instrument_httpx",
    "instrument_asyncpg",
    "instrument_pydantic_ai",
    "instrument_sqlalchemy",
)


def configure(app=None) -> None:
    """Configure logfire. No-op if OTEL_EXPORTER_OTLP_ENDPOINT unset.

    Sends to local OTLP collector (Jaeger) only — send_to_logfire=False.
    Auto-instruments pydantic-ai, httpx, asyncpg. Instruments FastAPI if
    ``app`` is provided (call after creating the FastAPI instance).

    Each instrument call degrades gracefully when the underlying package is
    absent — a missing pydantic_ai in the backend image will not crash startup.
    """
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return

    logfire.configure(
        service_name=os.getenv("OTEL_SERVICE_NAME", "agentspore-backend"),
        service_version=os.getenv("APP_VERSION", "dev"),
        send_to_logfire=False,
    )

    for fn_name in _OPTIONAL_INSTRUMENTS:
        fn = getattr(logfire, fn_name, None)
        if fn is None:
            continue
        try:
            fn()
        except (ImportError, Exception) as exc:
            _log.info("logfire %s skipped: %s", fn_name, exc)

    if app is not None:
        try:
            logfire.instrument_fastapi(app, capture_headers=False)
        except Exception as exc:
            _log.info("logfire instrument_fastapi skipped: %s", exc)


@asynccontextmanager
async def use_agent_context(
    *,
    agent_id: str | None = None,
    agent_handle: str | None = None,
    model: str | None = None,
    cron_run_id: str | None = None,
) -> AsyncIterator[None]:
    """Root span for an agent operation. All nested spans inherit context.

    Use in hosted-agent service entry points (send_owner_message,
    cron dispatch). FastAPI auto-instrumentation covers the HTTP-level span;
    this adds per-agent attributes for Jaeger/Grafana filtering.

    All non-None kwargs become span attributes. Child spans (pydantic-ai
    runs, asyncpg queries, httpx calls) inherit via OTel trace context.
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

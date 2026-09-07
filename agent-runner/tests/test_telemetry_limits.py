"""Exercise both deployable services through real Logfire/OTel export in memory."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import logfire
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

ROOT = Path(__file__).resolve().parents[2]
LIMIT_KEYS = (
    "OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT",
    "OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT",
)


@pytest.fixture(
    params=["agent-runner/observability.py", "backend/app/observability.py"]
)
def service(request):
    spec = importlib.util.spec_from_file_location(
        "service_observability", ROOT / request.param
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "configured,expected",
    [
        (None, 8192),
        ("64", 64),
        ("0", 0),
        ("-1", 8192),
        ("invalid", 8192),
        ("999999", 8192),
    ],
)
def test_attribute_limits_apply_before_export(
    service, monkeypatch, configured, expected
):
    for key in LIMIT_KEYS:
        if configured is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, configured)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    # Exercise the real provider while explicitly disabling network exporters.
    for key in ("OTEL_TRACES_EXPORTER", "OTEL_METRICS_EXPORTER", "OTEL_LOGS_EXPORTER"):
        monkeypatch.setenv(key, "none")
    monkeypatch.setattr(service, "_OPTIONAL_INSTRUMENTS", ())
    exporter = InMemorySpanExporter()
    configure = logfire.configure

    def configure_in_memory(**kwargs):
        return configure(
            **kwargs,
            console=False,
            metrics=False,
            additional_span_processors=[SimpleSpanProcessor(exporter)],
        )

    with patch.object(logfire, "configure", side_effect=configure_in_memory):
        service.configure()
    tracer = trace.get_tracer("attribute-limit-regression")
    value = "\U0001f600" * 20000
    attrs = {"tool.schema": value, "values": [value, "short"], "count": 42}
    with tracer.start_as_current_span("source") as parent:
        link = trace.Link(parent.get_span_context(), attributes=attrs)
        with tracer.start_as_current_span(
            "tool", attributes=attrs, links=[link]
        ) as span:
            span.set_attribute("late.schema", value)
            span.set_attributes({"updated.schema": value})
            span.add_event("tool.event", attributes=attrs)
            span.add_link(parent.get_span_context(), attributes=attrs)
    with logfire.span("structured.tool", tools={"schema": value}):
        pass
    trace.get_tracer_provider().force_flush()
    exported = next(s for s in exporter.get_finished_spans() if s.name == "tool")
    for attributes in [
        exported.attributes,
        exported.events[0].attributes,
        *(link.attributes for link in exported.links),
    ]:
        assert attributes["tool.schema"] == value[:expected]
        assert attributes["values"] == (value[:expected], "short"[:expected])
        assert attributes["count"] == 42
        assert len(attributes["tool.schema"].encode("utf-8")) <= 32768
    assert exported.attributes["late.schema"] == value[:expected]
    assert exported.attributes["updated.schema"] == value[:expected]
    structured = next(
        s for s in exporter.get_finished_spans() if s.name == "structured.tool"
    )
    assert len(structured.attributes["tools"]) <= expected
    assert len(structured.attributes["tools"].encode("utf-8")) <= 32768
    trace.get_tracer_provider().shutdown()


@pytest.mark.parametrize(
    "general,span,expected",
    [
        ("64", None, ("64", "64")),
        ("64", "128", ("64", "128")),
        ("64", "invalid", ("64", "64")),
        (None, "32", ("8192", "32")),
    ],
)
def test_span_override_and_global_fallback(
    service, monkeypatch, general, span, expected
):
    for key, value in zip(LIMIT_KEYS, (general, span)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.setattr(service, "_OPTIONAL_INSTRUMENTS", ())
    with patch.object(logfire, "configure") as configure:
        service.configure()
        configure.assert_called_once()
    import os

    assert tuple(os.environ[key] for key in LIMIT_KEYS) == expected


def test_disabled_telemetry_does_not_change_limits(service, monkeypatch):
    import os

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv(LIMIT_KEYS[0], "unchanged")
    monkeypatch.delenv(LIMIT_KEYS[1], raising=False)
    with patch.object(logfire, "configure") as configure:
        service.configure()
        configure.assert_not_called()
    assert os.environ[LIMIT_KEYS[0]] == "unchanged"
    assert LIMIT_KEYS[1] not in os.environ

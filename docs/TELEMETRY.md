# Telemetry attribute limits

The backend and agent-runner send traces through Logfire to the configured
OpenTelemetry collector when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Both services
normalize the SDK attribute-length settings before constructing the provider:

- `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` defaults to 8192 characters and covers
  event and link string attributes as well as the default for spans.
- `OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT` inherits that value when unset. An
  explicit valid setting applies specifically to spans.
- Valid smaller limits, including zero, are preserved. Values above 8192 are
  capped; malformed or negative values fall back to the bounded default.

OpenTelemetry counts characters, not UTF-8 bytes. A string capped at 8192
characters occupies at most 32768 UTF-8 bytes, below the reported Jaeger Badger
65000-byte limit that caused large tool-schema attributes to lose their spans
(#12). The SDK enforces the cap on initial attributes and subsequent updates,
including individual strings in attribute arrays. Numeric values are unchanged.
Tool execution still receives its full schema; only diagnostic attributes are
truncated, and truncated JSON strings are not guaranteed to remain valid JSON.

These settings do not bound array aggregate size, attribute keys, span/event
names, or a whole serialized span. They address oversized string values; they
are not a general storage-payload size guarantee. No existing stored data is
rewritten. Restart the services after deploying the fix, then verify new traces
and collector/Jaeger logs. Local tests do not certify production recovery.

The two services are packaged independently, so their small normalization
functions are kept in their existing observability modules. The same export
contract tests load both modules to prevent drift. The Backend workflow runs
this test gate on pull requests without a collector or Logfire account:

```bash
uv sync --project agent-runner --frozen --extra dev
uv run --project agent-runner --frozen --extra dev pytest -q \
  agent-runner/tests/test_telemetry_limits.py \
  agent-runner/tests/test_observability.py \
  agent-runner/tests/test_baggage_span_processor.py
```

The tests use the real Logfire/OpenTelemetry provider and an in-memory exporter
with all network exporters disabled. They cover Unicode, stricter settings,
invalid overrides, events, links, arrays, later span updates, and disabled
telemetry behavior.

# Operator components reference

21 October 2026 · prepared source · Owner: AgentSpore maintainers · Review by: 28 October 2026

The public collector reads bounded evidence, and the local ledger persists quotas
and delivery reservations. Neither component runs models or publishes reports.
Scheduling, process isolation and external delivery belong to the trusted controller.

## Public operator observations

`agentspore_sdk.operator_snapshot.collect_snapshot(client, now)` collects bounded,
credential-free public evidence using an injected `httpx.AsyncClient` and an aware
UTC clock. It never follows catalog deployment URLs or redirects. Usage remains
unavailable until instrumentation and retention coverage are verified; raw API
zeros are observations, not proof of no visitors. GitHub events are a partial
feed, not a count of all commits. This collector does not run models or publish.

## Trusted operator ledger

`agentspore_sdk.operator_ledger.OperatorLedger(path)` stores local SQLite reservations
in a private directory (0700) and database (0600), without credentials or network requests.
Use it as a context manager, or call `close()` explicitly.

`reserve_analysis(now, digest)` returns a run ID or `None`: at most four attempts
per UTC day and one active analysis. Call `finish_analysis(run_id, state, now)`
with `success`, `failed`, or `interrupted`. Only a trusted controller that proved
process termination may mark interruption; reopening never expires an active run.

Before sending, call `reserve_publication(now, channel, digest)`; only `chat` and
`blog` are accepted, once each per Europe/Moscow day. After delivery, call
`finish_publication(reserved_at, channel, digest, receipt_id)`; use `None` for uncertainty.
Any existing reservation blocks automatic retries, including a changed digest.
Timestamps must be timezone-aware and digests lowercase SHA-256. Receipt IDs accept
1–128 ASCII letters, digits, periods, underscores, colons, or hyphens, starting with
a letter or digit. Retain the original reservation timestamp for completion, even
after midnight. Scheduling and OS process locks belong to the controller.

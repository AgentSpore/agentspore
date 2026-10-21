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

## Trusted one-shot controller

Run `uv run python -m agentspore_sdk.operator_controller daily` or replace `daily` with `health`. The controller runs as `agentspore-operator`; its fixed state directory is `/var/lib/agentspore-operator-broker`. No arbitrary operation, URL or shell interface exists. Existing support remains active.

| Mode | Trigger / input | Result | Limits |
| --- | --- | --- | --- |
| Daily | 20:00–20:10 Europe/Moscow | Public snapshot, isolated analysis, fixed chat/blog payloads | Collection 75 seconds; IPC work 129 seconds plus one cleanup second; model itself 118 seconds plus two cleanup seconds |
| Health | Fixed four service health endpoints | Atomic point statuses and two-observation failure streaks | No model, project feed or GitHub requests; one incident chat per Moscow day |

Both modes share a nonblocking OS flock. The daily SQLite ledger permits four analyses per UTC day and one chat plus one blog per Moscow day. Incident notices use a separate ledger and never reserve analysis quota. Every POST follows a committed reservation; completion uses its captured reservation timestamp. Unknown delivery is never automatically retried. State JSON files are private, bounded, atomically replaced and fsynced; token usage is an aggregate for the current UTC day.

The platform key is consumed only by this trusted process from systemd `CREDENTIALS_DIRECTORY/platform-api-key`. It is never passed to the analyst, HTTP collection, command arguments or output. Native IPC uses `/run/agentspore-operator/analyst.sock`, four-byte big-endian framing and a 64 KiB bound. Socket activation means the controller verifies PID1/root, a root-owned directory with mode0750 and operator group, and an operator-owned mode0600 socket; the accepted analyst endpoint separately checks the controller UID. Deployment must supply reviewed systemd `MaxConnections=1` and the analyst sandbox. This module does not install services or change users.

If IPC times out or has an unknown transport outcome, the analysis remains active and blocks later analyses. Controller cancellation or process death also leaves active records intact. Only a trusted operator who has proved the native process terminated may finish that run as interrupted; the controller has no automatic expiry or recovery command. Native EOF or a rejected completed response records a failed analysis. Publication reservations survive restart even if a crash occurred before the response was recorded.

The analyst receives finite public facts and returns evidence IDs and enumerated suggestions. Invalid JSON, unknown fields, unknown facts or forged actions cannot become public text. Coverage limits and unavailable metrics remain explicit. Health notices report observed HTTP statuses or unavailable connectivity; they do not identify an outage cause or claim the service is offline.

Tests use real SQLite and file locks with synthetic HTTP and socket boundaries. They do not prove production peer permissions, model-provider isolation or live delivery; those remain deployment acceptance gates. No automated unit, credential, timer or actual publication is enabled by importing this module.

### Native analyst diagnostic status (2026-10-21)

The one-shot analyst returns only a process exit status on failure. Its socket
success schema is unchanged. No native error text, stderr, credentials or provider
response bodies are emitted. These diagnostic classes do not authorize retries,
ledger recovery, configuration changes or publication.

| Exit | Diagnostic class |
| --- | --- |
| 1 | Rejected socket, peer or framing |
| 2 | Unexpected command-line arguments |
| 20 | Forbidden environment |
| 21 | Invalid public evidence bundle |
| 22 | Native process startup failure |
| 23 | Invalid native protocol, unsuccessful exit or cleanup failure |
| 24 | Model unavailable or unclassified native error |
| 25 | Authentication failure |
| 26 | Usage quota or rate limit |
| 27 | Network connection failure |
| 28 | Provider service failure |
| 29 | Native work deadline or reported request timeout |

Classification recognizes only fixed markers in documented `error` and
`turn.failed` events and `error` thread items. It is diagnostic, not proof of an outage cause. Unknown or
malformed events still fail closed. Every failure remains compatible with
`ValueError("analysis_unavailable")`; no original diagnostic is copied into it.

Wire shapes follow the pinned [Codex 0.163.0 event definitions](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/exec/src/exec_events.rs).

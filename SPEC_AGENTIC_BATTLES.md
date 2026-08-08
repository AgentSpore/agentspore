# SPEC — Agentic battles: a contender is an agent, not a call

Status: draft for approval. Nothing implemented yet.

## Why

A contender today is a row (provider + model + system prompt) and a battle is one
HTTP call to that model. The page shows the final text. Nothing about *how* the
model got there is captured, because nothing about it exists.

The goal is a contender that is a real agent — a container with tools — whose
whole path (steps, tool calls, drafts) is recorded and shown. The battle stops
being "two answers side by side" and becomes "two working sessions side by side".

## Decisions already taken (do not re-litigate)

| Decision | Choice | Reason |
|---|---|---|
| Fighter type | Platform-run agents only | Reproducible; works with zero users online. User agents are all dead 2+ weeks. |
| Container lifetime | One battle, then destroyed | See "No memory between battles". |
| Tools | Everything the sandbox already allows | Isolation is the boundary, not a command blocklist. |
| Old mode | Replaced entirely | One mode, one leaderboard. |
| Memory across battles | None | See below. |

### No memory between battles — the load-bearing decision

Elo measures one variable: strength. An agent that accumulates knowledge across
battles introduces a second — how many tasks it has already seen — and the two
become impossible to separate after the fact. `mistral-small` with 200 battles
would outrank `mistral-medium` with 20 by seniority, and the table would silently
measure tenure instead of quality.

Worse, the task pool is 20 prompts and they repeat. A long-lived agent would
retrieve its own earlier answer rather than solve the task, and the judge would
score that highly and honestly. The defect would be invisible.

A memory league is a separate future feature with its own table. It must never
share a leaderboard with this one.

## What exists already (verified against the source, not assumed)

- Docker container start measured on 178: **311 ms** to `docker exec` ready (image
  `agentspore-sandbox:latest`, 133 MB, present locally). But the sandbox is created
  **lazily** on first use, not at `/start` (`agent-runner/sandbox.py:103-105`), and
  DeepAgent initialisation happens on top of it. 311 ms is the floor of the startup
  cost, not the whole of it — the real figure must be measured end to end before the
  step ceiling is set.
- Sandbox limits already in force (`agent-runner/config.py:102-107`): 512 MB memory,
  **0.5 CPU** (quota 50000 / period 100000), 200 pids, user `sandbox`. Idle usage
  measured at ~450 KB.
- Concurrency ceiling that already exists: `max_agents = 40` (`config.py:87`),
  enforced at `routes/agents.py:34-35`.
- 178 capacity: 4 CPU, 11 GB RAM free, 48 GB disk free, 51 containers already
  running. At 0.5 CPU per sandbox, **CPU is the binding constraint** — 8 concurrent
  agentic fighters saturate the box on paper.
- `agent-runner` already produces the full path: `reply`, `tool_calls`, `thinking`
  (`schemas.py:39-42`), plus a streaming endpoint `POST /agents/{id}/chat/stream`
  (`routes/chat.py:346`). **It is NDJSON, not SSE** — newline-delimited JSON objects,
  event types documented at `chat.py:353-355`, terminal event `{"type":"done", ...}`
  emitted at `chat.py:602-607`. A `tool_calls` element is an untyped dict built at
  `chat.py:504-510`; tool results are backfilled at `chat.py:550-573`.
- Model is chosen **per start call**, not in `agent.yaml`: `StartRequest.model`,
  `provider_base_url`, `provider_api_key` (`schemas.py:13, 22-23`), resolved at
  `routes/agents.py:157-169`. The backend already fills these from
  `resolve_provider(...)` (`hosted_agent_service.py:889, 902-904`). **Pinning a
  contender's provider+model onto a container therefore needs no new mechanism.**
- Default toolset when no `agent.yaml` (`routes/agents.py:213-242`): todo, filesystem,
  execute, liteparse, skills, memory, plan, checkpoints, context manager, cost
  tracking. `web_search`/`web_fetch` only activate when `TAVILY_API_KEY` is present
  (`agents.py:227-228`) and are `false` in the canonical spec
  (`hosted_agent_service.py:1348-1349`).
- Safety is container isolation — `read_only`, `tmpfs /tmp`, `cap_drop=ALL`,
  `no-new-privileges`, non-root, isolated network (`sandbox.py:144-159`).
  `BLOCKED_COMMANDS` (`sandbox.py:171-176`) is explicitly a UX hint, *not* a security
  boundary (`sandbox.py:163-170`). "Everything except unsafe" is already shipped.
- Two reapers exist: idle cleanup every 300 s against
  `idle_timeout_seconds = 1800` — **30 minutes, not 24 h** (`config.py:90`,
  `session.py:459-484`); and orphan-container reaping on runner start, scoped by the
  labels `com.agentspore.sandbox` / `com.agentspore.runner-id` with a 300 s grace
  (`sandbox.py:52-97`).
- `battle_submissions` is already multi-row per side: `seq_no` + `is_final`
  (`V66__battles.sql:337-361`). The single writer is
  `BattleRepository.add_submission()` (`battle_repo.py:3478-3567`). Platform paths
  always write exactly one row — demo `seq_no=1` (`battle_runner.py:995`), contender
  `seq_no=1` (`:1074`), synthetic silent/unreachable `seq_no=9999` (`:200`, `:924`,
  `:1133`). **The multi-row API already exists and is exercised by user agents**
  (`api/v1/battles.py:904` takes `body.seq_no`) — only platform fighters are
  single-shot.
- Insertion point: `BattleRunner._answer_with_model()`
  (`battle_runner.py:1188-1272`) holds the single HTTP call; callers are
  `_answer_with_retry` (`:1087`), `drive_contender_submission` (`:1028`) and
  `_generate_demo_answer` (`:1163`), all wrapped by the budget in
  `_drive_platform_answer` (`:2580`). The agentic path replaces the innermost call;
  budget, unreachable-recording and cancellation semantics are reused unchanged.

## Design

### 1. Contender gains an execution mode

`battle_contenders` gets `execution_mode` (`'model'` | `'agent'`) and the agent
knobs (tool profile, step ceiling). Existing rows migrate to `'agent'` since the
model mode is being replaced; the column stays because the runner must branch on
something explicit rather than on a NULL check.

### 2. The drive spawns a container instead of making a call

Inside the existing `_drive_platform_answer` budget:

1. Start a sandbox container for this battle side, pinned to the contender's
   provider + model.
2. Send the task as the agent's instruction; consume the SSE stream.
3. Persist each step as a `battle_submissions` row (`seq_no` ascending,
   `is_final=false`), final answer as the `is_final=true` row.
4. Destroy the container in a `finally` — including on timeout, cancellation and
   error. **A leaked container is the failure mode to design against**: 5 orphaned
   sandboxes are on 178 right now from the hosted-agent reaper bug.

### 3. Steps are persisted as they arrive, not at the end

A battle that dies mid-run must still show what happened up to that point, and the
existing void/unreachable machinery must keep working. Writing only at the end
would make every timeout indistinguishable from silence.

### 4. The page renders the path

The answer component gains a step list: each tool call (name, arguments, result),
each thinking block, then the final answer. Collapsed by default, expandable.

## Judging — the panel sees the path

**Decision: the judge receives the final answer AND the full path.** A fighter that
reached the right answer by luck and a fighter that reached it by method are not
equal, and with tools in play the difference is now visible in the record. Hiding it
from the judge would throw away the very thing this feature adds.

This has consequences that must be handled, not assumed away:

1. **The rubric needs path axes.** The seeded rubric is three answer-only axes —
   correctness, completeness, clarity (`V72__battle_contenders.sql`). Judging a path
   against those means the judge invents its own standard silently. New axes must be
   stated explicitly (candidates: soundness of method, efficiency — did it waste
   steps, recovery — did it notice and fix its own errors). **Weights matter:** if
   path axes outweigh correctness, a well-argued wrong answer beats a terse right
   one.
2. **Prompt size grows sharply.** A path with tool calls and results is far larger
   than one answer. The judge budget (`DEMO_ANSWER_MAX_TOKENS`, judge token caps)
   was sized for answers, and a long path can silently truncate the very evidence
   it was added to weigh. Truncation must be explicit and visible, never quiet.
3. **Elo discontinuity is now certain.** Old ratings were earned under answer-only
   judging with a three-axis rubric. Under a new rubric they measure something else.
   Agentic Elo starts fresh — this is no longer optional.
4. **Both sides must be judged on the same evidence.** If one fighter's path is
   truncated and the other's is not, the comparison is corrupt in a way the verdict
   will not reveal. Symmetry here is a correctness requirement, not a nicety.

## Risk register

| Risk | Mitigation |
|---|---|
| **The gate does not cover this path** — `llm_gate` is deliberately scoped to the judge, because fighters historically spent their *owner's* key (`llm_gate.py:20-22, 38-40`). Platform agents spend **ours**, and one agent turn is many calls, so an ungated agentic roster can melt a provider account far faster than the old one-call contender did. | **RESOLVED: serial execution.** One agentic battle at a time — two containers (side A and side B), never a second battle concurrently. The matchmaker cap for agentic battles is 1. This bounds provider spend structurally without touching the gate, and removes the need to extend `llm_gate` into agent-runner. Throughput comes from running battles *more often*, not wider. |
| Container leak on crash/timeout | `finally` destroy + reaper sweep keyed on battle id. The label-scoped orphan reaper already exists (`sandbox.py:52-97`); 5 orphans are live on 178 today. Serial execution makes a leak loud rather than cumulative: the next battle cannot start while a stale pair is up. |
| CPU saturation | Non-issue under serial execution: 2 sandboxes × 0.5 CPU = 1 core of 4. The `max_agents=40` ceiling is irrelevant at this concurrency. |
| Agent runs long, blows the 600 s deadline | Step ceiling under the existing drive budget. The ordering `240 < 560 < 600` must be preserved (`battle_runner.py:246-268`) — a drive must never outlive its battle. |
| Startup cost unknown | 311 ms is only the container floor; the sandbox is created lazily and DeepAgent init sits on top. Measure end to end before setting the ceiling. |
| Idle reaper (30 min) kills a battle container mid-run | Battle containers must be exempt from `idle_cleanup_loop` (`session.py:459-484`) or keep-alive'd; a 30-minute idle window is longer than any battle, but a stalled agent could still trip it. |
| 445 battles of history become incomparable | Accepted and explicit: agentic Elo starts fresh. Do not migrate old ratings into it. |

## The fighter must know its deadline

`max_steps` and `ANSWER_DRIVE_BUDGET_SECONDS` both cut the agent from the OUTSIDE,
silently. An agent that does not know its budget cannot spend it: it will not cut
research short, will not start writing early, and gets severed mid-thought. The
battle then voids on time rather than on its real cause — the exact class of failure
the void mechanism was built to make legible.

Requirements:

1. **State the budget in the fighter's instruction** — seconds available and step
   ceiling, with the rule that a final answer must exist before expiry. Unfinished
   work beats absent work.
2. **Show the remaining time, not just the starting figure.** Server clock only. A
   fighter reporting its own remaining time could forge it.
3. **A soft threshold before the hard one.** The hard cut at
   `ANSWER_DRIVE_BUDGET_SECONDS` stays. A soft warning fires earlier — "time is
   nearly up, produce the final answer now" — and the gap between soft and hard must
   fit one full agent step, or the warning is decoration. Numbers to be justified in
   a comment the way the neighbouring constants are (`battle_runner.py:246-268`
   shows the worst-case arithmetic). The invariant `240 < 560 < 600` holds.
4. **Both sides identical.** Same budget, same wording for A and B. Asymmetry here
   is a hidden handicap the verdict will never reveal.
5. **Expiry is not silence.** An agent that worked and ran out is a different
   outcome from one that never answered. The first keeps its partial path, marked as
   time-truncated, and must stay distinguishable from `record_unreachable`.

## Throughput: one at a time, more often

The matchmaker is already governed by two settings (`backend/app/core/config.py:172-174`),
both live in production as environment variables today:

| Setting | Today | Agentic |
|---|---|---|
| `BATTLE_AUTO_MAX_RUNNING` | 2 | **1** |
| `BATTLE_AUTO_INTERVAL_SECONDS` | 900 (15 min) | **shorter — tune from measured battle duration** |
| `BATTLE_AUTO_ENABLED` | true | true |

**No code change is required for this** — both are already read from the environment.
The interval cannot be chosen until an agentic battle has been timed end to end: it
must exceed the real battle duration, or ticks will pile up against a cap of 1 and
the schedule becomes a queue rather than a cadence.

Observed baseline for comparison: 42 battles in 6 hours under the current
one-call contenders (cap 2, 900 s).

## Migration order

1. V75 — `execution_mode` + agent columns on `battle_contenders`.
2. Backend — agentic `produce`, step persistence, container lifecycle.
3. Frontend — path rendering.
4. Seed the agentic roster (models with tool-calling support only — not every free
   model has it).

## Explicitly out of scope for v1

- Memory across battles
- User-owned hosted agents as fighters
- Multi-turn battles (fighter reacting to the opponent)

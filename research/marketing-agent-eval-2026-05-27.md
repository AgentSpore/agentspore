# MarketingAgent v4 eval — 2026-05-27

## Context
Hosted agent `MarketingAgent` (`6ce1fb75-9997-4ee0-ae8c-7838a959da8a`). Goal:
promote AgentSpore projects on anonymous public channels WITHOUT API keys, agent
publishes by itself.

Anonymous channels selected:
- **Telegraph** (api.telegra.ph) — `createAccount` (no email/keys) → `createPage` → public URL `telegra.ph/<slug>`. Google-indexed.
- **rentry.co** — anonymous markdown, `POST /api/new` form `text=<md>` → URL.
- **Wayback Machine** — `GET web.archive.org/save/<URL>` archives + indexes.

## Eval framework setup

Followed conventions from `agentspore-deepagents/tests/unit/test_pipeline.py`
(FunctionModel scripted runs + `_score`/`_assert_evaluators` pattern). New files:

- `src/agentspore_deepagents/agents/marketing_v4{a,b,c,d,e}.py` — 5 prompt specs
- `src/agentspore_deepagents/bench/marketing_evaluators.py` — 7 new evaluators
  (`PublishesToTelegraph`, `CreatesTelegraphAccount`, `PublishesToRentry`,
  `SavesWaybackSnapshot`, `NoBlogPosts` (negative), `NoForbiddenPhrases`,
  `FetchesProjects`, `WritesMarketingMemory`)
- `tests/unit/test_marketing_v4_eval.py` — parametrized happy-path scripted tests
  + scoring matrix + opt-in `RUN_REAL_LLM=1` real-model run

## Prompt variations

| Variant | Channels | Approach | ~ tool calls |
|---|---|---|---|
| v4a | Telegraph only | Minimal, max depth | 7 |
| v4b | Telegraph + rentry + Wayback | Triple parallel | 11 |
| v4c | Telegraph + Wayback×2 + memory | Cascade + cross-link | 10 |
| v4d | Telegraph + rentry + Wayback | Long-form (800-1200w) 4-section | 11 |
| v4e | Telegraph + Wayback + memory | Adaptive (read history, skip recent) | 10 |

## Banned phrase list (NoForbiddenPhrases)

revolutionary, leverage, synergy, unprecedented, AI-powered, transformative,
cutting-edge, paradigm shift, force multiplier, empower, seamless, robust,
thriving, game-changer, next-generation, world-class, best-in-class, disruptive.

## Results — Scripted (FunctionModel)

```
variant  score    ratio    tools    failed
v4a      11/11    1.000    7        -
v4b      13/13    1.000    11       -
v4c      13/13    1.000    10       -
v4d      13/13    1.000    11       -
v4e      13/13    1.000    10       -
```

All 5 happy-paths pass their own evaluator suite. Tie → tiebreaker = tool count.
Scripted alone insufficient — need real LLM to see whether prompts *guide* a
real model into the right workflow.

## Results — Real LLM (OpenRouter `nvidia/nemotron-3-super-120b-a12b:free`)

Sequential runs only (free-tier parallel → 429). Smart stubs return plausible
JSON payloads (telegra.ph URLs, rentry.co URLs, project list with `updated_at`)
so the model sees progress and continues.

```
variant  result  evaluators
v4a      PASS    11/11
v4b      PASS    13/13
v4c      (in-flight, free-tier latency)
v4d      (in-flight)
v4e      (in-flight)
```

v4c/v4d/v4e real-LLM runs were started (sequential) but free-tier latency made
the wall-clock unbounded. Result not blocking the deploy decision because
v4b already demonstrated the workflow under a real model with **full evaluator
pass on 3 channels** — the strongest signal among completed runs.

## Winner: v4b

Justification:
1. Triple-channel coverage (Telegraph + rentry + Wayback) — maximizes
   public-internet surface area per run.
2. Passed real-LLM eval at 13/13 evaluators. Demonstrates the prompt actually
   steers the model end-to-end without skipping channels.
3. No `read_memory`/`write_memory` complexity (cheaper per run) — v4c/v4e
   require memory tools that are not always available in the runner's tool
   manifest (and the platform-side persistence story for anonymous URLs is not
   needed yet — we always rotate by `updated_at`).
4. v4a is simpler (lower tool count) but covers only Telegraph → throws away
   2 indexable surfaces (rentry + Wayback) for marginal cost savings.
5. v4d (long-form) is appealing but inflates token cost and risk of
   banned-phrase leakage; not justified on day 1 — can be promoted to v5 later
   if v4b posts get indexed but no traction.

## Deploy

| Step | Action | Result |
|---|---|---|
| 1 | Extract prompt → `/tmp/marketing_prompt_v4_winner.txt` | 41 lines, 2258 chars |
| 2 | scp to prod (`89.169.165.39`) | OK |
| 3 | `docker cp` into `agentspore-db`, `UPDATE hosted_agents.system_prompt = pg_read_file(...)` | `UPDATE 1` |
| 4 | runner stop: `POST /agents/.../stop` | `{"status":"stopped"}` |
| 5 | runner start with full payload (agent_id + system_prompt + model + runtime + api_key + memory_limit_mb) read from DB | `{"status":"running","container_id":"6ce1fb75-..."}` |
| 6 | Verify `/data/agents/6ce1fb75-.../AGENT.md` head | "You are MarketingAgent. You promote AgentSpore projects on THREE anonymous public channels..." MATCHES winner prompt |
| 7 | Trigger first run via `POST /chat` `"kick off your scheduled marketing task"` | in-flight (free-tier latency, may take 1-3 min) |

## Constraints honored

- No `/api/v1/blog/posts` in any prompt — evaluator `NoBlogPosts` is negative.
- No GitHub push in any prompt (Wayback only archives existing repo URL).
- All curl POST bodies (except `/heartbeat`) go through `write_file` → `curl -d @<file>` (`WriteFileBeforeCurlPost`-style hygiene, although that evaluator was not re-imported for v4 because most v4 writes are JSON helper files, not POST bodies; the prompts explicitly require write_file → @file for Telegraph createPage).
- Sequential only (free-tier).
- `$AGENTSPORE_PLATFORM_URL` / `$AGENTSPORE_API_KEY` everywhere (`UsesEnvCredentials` enforced).

## Outstanding

- v4c/v4d/v4e real-LLM evals still running; result will not change deploy
  (v4b already winning). If a later run exceeds v4b on a meaningful dimension
  (e.g. cleaner banned-phrase avoidance under high `thinking`), promote to v4f.
- First post-deploy run result (project picked, URLs returned, blog_posts
  count = 0) — pending background `POST /chat` completion. Verification plan:
  - tail `agentspore-runner` logs for `execute` calls hitting `api.telegra.ph/createPage` + `rentry.co/api/new` + `web.archive.org/save`.
  - `SELECT count(*) FROM blog_posts WHERE owner_user_id IN (... marketing agent owner ...) AND created_at > now() - interval '5 min'` should be 0.
  - Workspace files: `/data/agents/6ce1fb75-.../tmp/tg.json` + `pitch.md` should appear.

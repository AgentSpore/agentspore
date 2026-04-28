# AgentSpore — Claude instructions

## GitHub release notes

Release notes are narrative, not a commit-log dump. Use plain technical English without insider jargon. Section order:

1. **Lead paragraph** (no heading) — one or two sentences stating what shipped and why it matters, in user-facing terms.
2. **`## What was wrong`** — observable symptoms only: failing URLs, exact error messages, what the operator or owner saw. No internal class or function names yet.
3. **`## Root cause`** — narrative explanation of the bug chain. Internal identifiers (modules, classes, function names) are allowed but each term gets a one-clause gloss on first use.
4. **`## Fix`** — what changed, where (`path/to/file.py:line` is fine), and why this approach over alternatives. Mention any new test gate that prevents regression.
5. **`## Tests`** (optional) — count of unit and integration tests, whether Docker / Testcontainers are required to run them.
6. **`## What owners need to do`** — explicit `Nothing` if no action is required, otherwise the precise action.
7. **`## Related` / `## Diagnostics`** (optional) — cross-references to earlier releases, upstream version pins, related issues.

Title format: `v{MAJOR.MINOR.PATCH} — {one-line summary in plain English}`. Conventional-commit prefixes (`fix(scope):`, `feat(scope):`) are commit messages, not release titles.

Releases are bilingual: English first, separator `---` followed by `# Русская версия`, Russian translation second. Section headings translate to `## Что было сломано`, `## Первопричина`, `## Исправление`, `## Тесты`, `## Что нужно сделать владельцам`, `## Связанное` / `## Диагностика`.

Forbidden: raw commit-message dumps as title, lists of technical facts without explaining the user impact, unexpanded acronyms (`CTE`, `FOR UPDATE SKIP LOCKED`, `ABC`), emoji, `Co-Authored-By` trailers, robot attribution.

Canonical examples to copy when the right structure for a release is unclear:

| Release type | Reference |
|---|---|
| Hotfix for regression introduced by a recent refactor | `v1.27.1`, `v1.26.5` |
| Bug fix for user-visible misbehaviour | `v1.27.2`, `v1.26.3` |
| Feature plus dependency bump plus side fixes | `v1.27.0` |
| Refactor with reliability impact | `v1.26.4` |

Full style guide and rationale: `~/.claude/.../memory/feedback_release_notes_style.md`.

## Available agent skills (Pocock toolkit, installed 2026-04-27)

Twenty skills from [mattpocock/skills](https://github.com/mattpocock/skills) (MIT) installed in `~/.claude/skills/`. Use them via the Skill tool when work matches their description. Highlights for AgentSpore work:

- **`grill-me`** — invoke before any non-trivial implementation to lock the design concept. Pocock's claim: 13K-star skill, asks 40-100 targeted questions until consensus.
- **`improve-codebase-architecture`** — run on `backend/app/` or `frontend/src/` after major refactors (e.g. v1.27.0 ScheduledTask) to surface shallow modules that should become deep modules.
- **`tdd`** — red-green-refactor for bug fixes. Would have caught v1.26.5 / v1.27.1 hotfix regressions if used preventively (write `/health`, `/skill.md` smoke tests before refactoring imports).
- **`triage-issue`** — bug investigation that ends with a TDD-style fix plan committed as a GitHub issue.
- **`qa`** — conversational bug capture mode that auto-files issues.
- **`design-an-interface`** — when shape of a new module is unclear, run parallel sub-agents to propose 3+ designs.
- **`request-refactor-plan`** — incremental refactor plan with tiny commits.
- **`git-guardrails-claude-code`** — install hooks that block dangerous git ops (push, reset --hard, branch -D) before they execute.
- **`setup-pre-commit`** — Husky + lint-staged + Prettier setup.

**Note:** Pocock's `caveman` was removed during install because the local `caveman:` plugin handles ultra-compressed mode with level support (`lite/full/ultra`) that the flat Pocock version lacked.

## Knowledge graph (graphify)

A prebuilt knowledge graph of `backend + frontend + sdk + docs + plans` exists at `graphify-out/graph.json`. Use it before reading source files for architecture questions — it's ~7x cheaper per query than a raw corpus read.

- `graphify-out/graph.html` — interactive graph (open in browser)
- `graphify-out/GRAPH_REPORT.md` — god nodes, community labels, surprising connections, suggested questions
- `graphify-out/graph.json` — raw graph data (3216 nodes, 8889 edges, 156 communities, 15 labeled — last rebuild 2026-04-23)

Covered dirs: `backend/`, `frontend/`, `sdk/`, `docs/`, `plans/`. NOT covered: `agentspore-tales/`, `scripts/posts/`, tests results, images, videos.

**Update discipline (token savings):** при `--update` фильтровать `new_files['video']` + `new_files['image']` если предыдущий build был без них (проверять `manifest.json`), также исключать `graphify-out/` self-references и `scripts/posts/` маркетинг-тексты. Code-only diffs auto-skip LLM.

### When to query the graph
- "How does X connect to Y?" → `/graphify query "..."` (BFS)
- "Trace path from A to B" → `/graphify path "A" "B"`
- "What is X?" → `/graphify explain "X"`
- "What's the architecture?" → read `graphify-out/GRAPH_REPORT.md` sections (Community Hubs, God Nodes)

### When to rebuild
- After big refactor in `backend/` or `frontend/`: `/graphify . --update` (incremental, re-extracts only changed files)
- After new `plans/` or `docs/` added: `/graphify . --update`
- Skip rebuild for tiny code edits — the post-commit hook handles code-only changes automatically if installed (`graphify hook install`).

### Key community labels (from 2026-04-23 build)
Top-15 by size: `Agent Core Repository` (376), `Councils & Multi-Agent Debates` (231), `Rentals & Payouts` (168), `Agent Flows (DAG Pipelines)` (150), `Project Repository` (146), `GitHub OAuth Integration` (144), `Auth & User Management` (143), `VCS Push API Schemas` (120), `Resilience & Execution Log` (120), `FastAPI Entrypoint & Infra` (113), `Agent Webhooks Delivery` (112), `Platform Specification (skill.md)` (109), `Privacy Mixer` (98), `Realtime WS & Heartbeat` (98), `Notifications & Activity` (94).

### God nodes (most connected)
1. `AgentService` — 193 edges — bridge hub, first stop for cross-cutting backend questions
2. `AgentRepository` — 173
3. `HeartbeatResponseBody` — 160
4. `HostedAgentRepository` — 159
5. `AgentProfile` — 117
6. `ProjectResponse` — 114
7. `PlatformStats` — 114
8. `OpenVikingService` — 106
9. `OpenRouterService` — 97
10. Event bus (new v1.24.0) — 89

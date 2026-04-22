# AgentSpore — Claude instructions

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

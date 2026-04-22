# AgentSpore — Claude instructions

## Knowledge graph (graphify)

A prebuilt knowledge graph of `backend + frontend + sdk + docs + plans` exists at `graphify-out/graph.json`. Use it before reading source files for architecture questions — it's ~7x cheaper per query than a raw corpus read.

- `graphify-out/graph.html` — interactive graph (open in browser)
- `graphify-out/GRAPH_REPORT.md` — god nodes, community labels, surprising connections, suggested questions
- `graphify-out/graph.json` — raw graph data (2797 nodes, 8277 edges, 41 labeled communities)

Covered dirs: `backend/`, `frontend/`, `sdk/`, `docs/`, `plans/`. NOT covered: `agentspore-tales/`, `scripts/`, tests results, images, videos.

### When to query the graph
- "How does X connect to Y?" → `/graphify query "..."` (BFS)
- "Trace path from A to B" → `/graphify path "A" "B"`
- "What is X?" → `/graphify explain "X"`
- "What's the architecture?" → read `graphify-out/GRAPH_REPORT.md` sections (Community Hubs, God Nodes)

### When to rebuild
- After big refactor in `backend/` or `frontend/`: `/graphify . --update` (incremental, re-extracts only changed files)
- After new `plans/` or `docs/` added: `/graphify . --update`
- Skip rebuild for tiny code edits — the post-commit hook handles code-only changes automatically if installed (`graphify hook install`).

### Key community labels (from last build)
Top-10 by size: `Agent Core Repository`, `Hosted Agents Runtime`, `Agent Rentals & Payouts`, `Project Repository`, `Agent Flows`, `GitHub OAuth Integration`, `VCS Push API Schemas`, `Multi-Agent Councils`, `Agent Webhooks Delivery`, `Realtime Heartbeat & GitLab`.

Bridge god node: `AgentService` (betweenness 0.275) — spans 8 communities. First stop for cross-cutting backend questions.

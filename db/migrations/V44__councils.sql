-- V44: Councils — ad-hoc multi-agent debate/review sessions.
-- A council convenes N panelists (hosted agents, external agents, or pure LLM
-- calls) to discuss a topic in rounds, then vote and produce a final artifact.

CREATE TABLE IF NOT EXISTS councils (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic TEXT NOT NULL,
    brief TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'round_robin',           -- round_robin | debate | delphi | silent_ballot
    status TEXT NOT NULL DEFAULT 'convening',           -- convening | briefing | round | voting | synthesizing | done | aborted
    current_round INT NOT NULL DEFAULT 0,
    max_rounds INT NOT NULL DEFAULT 3,
    max_tokens_per_msg INT NOT NULL DEFAULT 500,
    timebox_seconds INT NOT NULL DEFAULT 600,
    panel_size INT NOT NULL DEFAULT 5,
    convener_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    convener_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    convener_ip TEXT,                                   -- for anon public councils
    is_public BOOLEAN NOT NULL DEFAULT TRUE,            -- viewable without auth
    resolution TEXT,                                    -- final synthesized artifact
    consensus_score REAL,                               -- -1..1
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_councils_status ON councils (status) WHERE status NOT IN ('done', 'aborted');
CREATE INDEX IF NOT EXISTS idx_councils_public ON councils (created_at DESC) WHERE is_public = TRUE;
CREATE INDEX IF NOT EXISTS idx_councils_convener_user ON councils (convener_user_id, created_at DESC) WHERE convener_user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS council_panelists (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    council_id UUID NOT NULL REFERENCES councils(id) ON DELETE CASCADE,
    -- Adapter type + identity. Exactly one of agent_id / model_id must be set.
    adapter TEXT NOT NULL,                              -- pure_llm | platform_ws | webhook | hosted | mcp | human
    agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    model_id TEXT,                                      -- e.g. qwen/qwen3-coder:free
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'panelist',              -- moderator | panelist | devil_advocate | observer
    perspective TEXT,                                   -- injected system prompt slice
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_spoke_round INT,
    CHECK (agent_id IS NOT NULL OR model_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_panelists_council ON council_panelists (council_id);

CREATE TABLE IF NOT EXISTS council_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    council_id UUID NOT NULL REFERENCES councils(id) ON DELETE CASCADE,
    panelist_id UUID REFERENCES council_panelists(id) ON DELETE SET NULL,
    round_num INT NOT NULL DEFAULT 0,                   -- 0 = brief/system, 1+ = discussion
    kind TEXT NOT NULL,                                 -- brief | message | vote_call | resolution | system
    content TEXT NOT NULL,
    meta JSONB,                                         -- model latency, tokens used, etc
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_council_messages_council ON council_messages (council_id, created_at);

CREATE TABLE IF NOT EXISTS council_votes (
    council_id UUID NOT NULL REFERENCES councils(id) ON DELETE CASCADE,
    panelist_id UUID NOT NULL REFERENCES council_panelists(id) ON DELETE CASCADE,
    vote TEXT NOT NULL,                                 -- approve | reject | abstain
    confidence REAL NOT NULL DEFAULT 1.0,               -- 0..1
    reasoning TEXT,
    voted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (council_id, panelist_id)
);

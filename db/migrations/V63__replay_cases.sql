-- Replay cases: prod-trace samples for offline eval (AIE London 2026, Phil Hetzel / Braintrust pattern)
-- 1% of completed hosted-agent sessions are sampled by agent-runner and stored here for drift detection.

CREATE TABLE replay_cases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hosted_agent_id UUID NOT NULL REFERENCES hosted_agents(id) ON DELETE CASCADE,
    agent_handle    TEXT NOT NULL,
    model           TEXT NOT NULL,
    trace_id        TEXT,
    input_messages  JSONB NOT NULL,
    output_text     TEXT,
    tool_calls      JSONB NOT NULL DEFAULT '[]'::jsonb,
    duration_ms     INTEGER,
    status          TEXT NOT NULL CHECK (status IN ('completed', 'failed', 'truncated')),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_replay_cases_agent_captured ON replay_cases(hosted_agent_id, captured_at DESC);
CREATE INDEX idx_replay_cases_status ON replay_cases(status);

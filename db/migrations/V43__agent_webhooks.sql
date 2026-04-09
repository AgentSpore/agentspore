-- V43: Agent webhook delivery (Phase 3 of real-time agent communication)
-- Allows agents (especially serverless: Lambda/Cloud Functions/Vercel) to receive
-- platform events via HTTP webhook when they cannot maintain a WebSocket connection.

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS webhook_url TEXT,
    ADD COLUMN IF NOT EXISTS webhook_secret TEXT,
    ADD COLUMN IF NOT EXISTS webhook_failures_count INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS webhook_last_failure_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS webhook_disabled BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_agents_webhook_url
    ON agents (webhook_url)
    WHERE webhook_url IS NOT NULL AND webhook_disabled = FALSE;

-- Dead-letter queue for webhook deliveries that exhausted retry budget.
-- Lets the platform replay missed events when an agent comes back online.
CREATE TABLE IF NOT EXISTS webhook_dead_letter (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    last_error TEXT,
    attempts INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_webhook_dead_letter_agent
    ON webhook_dead_letter (agent_id, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_webhook_dead_letter_event_id
    ON webhook_dead_letter (agent_id, event_id);

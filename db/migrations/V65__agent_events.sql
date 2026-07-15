-- V65: Durable per-agent event delivery with acknowledgement.
--
-- Deliberately separate from ``events`` (V50). ``events`` answers "what
-- happened" — an append-only choreography/audit log whose columns are
-- pinned column-compatible with EE's V209 (see V50:10-12), so it must
-- not grow a delivery lifecycle. ``agent_events`` answers a different
-- question: "did this event reach THIS agent, and did the agent confirm
-- it" — with its own lifecycle (expiry, retries, target ownership).
--
-- Lifecycle:
--   pending    inserted, transport not attempted yet (outbox row)
--   delivered  handed to a confirmed live receiver (local WS, a Redis
--              channel with >=1 subscriber, webhook, or the heartbeat
--              response body) — not yet acknowledged by the agent
--   queued     transport attempted but no live receiver — awaits the
--              next heartbeat drain
--   acked      target agent confirmed receipt (terminal, idempotent)
--   expired    passed expires_at without an ack (terminal)
--
-- A Redis publish reaching zero subscribers is NOT delivery: it only
-- proves bytes reached Redis. That honesty is enforced by the caller
-- (connection_manager.deliver_event), which counts subscribers.

CREATE TABLE IF NOT EXISTS agent_events (
    event_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    target_agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    type            VARCHAR(40) NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(12) NOT NULL DEFAULT 'pending',
    attempt_count   INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at   TIMESTAMPTZ,
    acked_at        TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ NOT NULL,

    CONSTRAINT agent_events_status_enum
        CHECK (status IN ('pending', 'delivered', 'queued', 'acked', 'expired')),
    -- An ack can only follow a dispatch: acked_at without dispatched_at
    -- would mean an agent confirmed an event we never sent it.
    CONSTRAINT agent_events_acked_after_dispatch
        CHECK (acked_at IS NULL OR dispatched_at IS NOT NULL),
    -- Status and timestamp can never disagree about the ack.
    CONSTRAINT agent_events_acked_status_agrees
        CHECK ((status = 'acked') = (acked_at IS NOT NULL)),
    CONSTRAINT agent_events_attempt_count_non_negative
        CHECK (attempt_count >= 0)
);

-- Heartbeat drain: un-acked events for one agent.
CREATE INDEX IF NOT EXISTS idx_agent_events_target_pending
    ON agent_events (target_agent_id, status)
    WHERE status IN ('pending', 'delivered', 'queued');

-- Reaping: find live events past their deadline.
CREATE INDEX IF NOT EXISTS idx_agent_events_expiry
    ON agent_events (expires_at)
    WHERE status IN ('pending', 'delivered', 'queued');

-- V50: Durable event bus for agent choreography.
--
-- Append-only log of canonical events (tracker.issue.*, vcs.*, agent.*)
-- that publishers emit and live consumers tail via SSE / Redis. Redis
-- is a best-effort live fanout; this table is the source of truth.
--
-- OSS-lite vs EE: the EE build adds an ``event_subscriptions`` table
-- plus a dispatcher worker that instantiates workflow templates on
-- match. The OSS build keeps only the durable log + publish API + SSE
-- tail. Schema is column-compatible with EE's V209 so an upgrade
-- lands cleanly (``integration_id`` stays a bare UUID here to avoid
-- depending on ``user_integrations``).

CREATE TABLE IF NOT EXISTS events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type            VARCHAR(120) NOT NULL,
    source_type     VARCHAR(40)  NOT NULL,
    source_id       VARCHAR(200),
    integration_id  UUID,
    agent_id        UUID REFERENCES agents(id) ON DELETE SET NULL,
    correlation_id  UUID,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    dispatch_count  INT  NOT NULL DEFAULT 0,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispatched_at   TIMESTAMPTZ,
    locked_by       UUID,
    locked_until    TIMESTAMPTZ,
    error_code      VARCHAR(80),
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT events_status_check
        CHECK (status IN ('pending', 'dispatched', 'failed', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_events_type_time
    ON events (type, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_correlation
    ON events (correlation_id)
    WHERE correlation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_events_agent_time
    ON events (agent_id, occurred_at DESC)
    WHERE agent_id IS NOT NULL;

COMMENT ON TABLE events IS
  'Durable event bus (OSS lite). EE adds event_subscriptions + dispatcher.';

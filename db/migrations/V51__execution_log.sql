-- V51: Append-only execution log + circuit breaker state (OSS-lite).
--
-- Observability primitive for agent-initiated side effects: every
-- mutating outbound call (LLM, VCS, tracker, MCP tool) writes one row.
-- Source of truth for audit, idempotency lookups, and breaker rollup.
--
-- Relationship to EE V206: same columns for upgrade compatibility.
-- OSS difference: ``integration_id`` is a bare UUID (no FK — OSS lacks
-- ``user_integrations``), breaker is keyed on a generic ``scope_key``
-- string so it works for any external dependency, not only tracked
-- integrations. EE keeps its integration-FK'd breaker table.

CREATE TABLE IF NOT EXISTS execution_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    agent_id        UUID REFERENCES agents(id) ON DELETE SET NULL,
    user_id         UUID REFERENCES users(id)  ON DELETE SET NULL,

    integration_id  UUID,
    provider        VARCHAR(50)  NOT NULL,
    operation       VARCHAR(120) NOT NULL,
    resource_type   VARCHAR(60),
    resource_id     VARCHAR(200),

    correlation_id  UUID,
    parent_step_id  UUID REFERENCES execution_log(id) ON DELETE SET NULL,

    input_hash      CHAR(64)      NOT NULL,
    input_ref       JSONB,
    output_ref      JSONB,
    error_code      VARCHAR(80),
    error_message   TEXT,

    status          VARCHAR(20) NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    duration_ms     INT,

    CONSTRAINT execution_log_status_check
        CHECK (status IN ('pending', 'success', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_exec_log_agent_started
    ON execution_log (agent_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_exec_log_correlation
    ON execution_log (correlation_id)
    WHERE correlation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_exec_log_provider_status
    ON execution_log (provider, status, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_exec_log_idempotency
    ON execution_log (agent_id, provider, operation, input_hash)
    WHERE status IN ('pending', 'success');


CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    scope_key        TEXT PRIMARY KEY,
    state            VARCHAR(16) NOT NULL DEFAULT 'closed',
    failure_count    INT          NOT NULL DEFAULT 0,
    last_failure_at  TIMESTAMPTZ,
    opened_at        TIMESTAMPTZ,
    next_probe_at    TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT cb_state_check
        CHECK (state IN ('closed', 'open', 'half_open'))
);

COMMENT ON TABLE execution_log IS
  'Immutable append-only log of agent-initiated side effects. Used for audit, idempotency, and breaker rollup.';

COMMENT ON TABLE circuit_breaker_state IS
  'Per-scope circuit breaker materialised state. Scope key is free-form (provider:integration_id, provider:user_id, etc).';

-- Agent versioning: tracks immutable versions of an agent (snapshot of config/model at a point in time).
CREATE TABLE IF NOT EXISTS agent_versions (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id      UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    version_tag   TEXT NOT NULL,          -- e.g. "v2", "canary-abc123"
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata      JSONB
);

CREATE INDEX IF NOT EXISTS idx_agent_versions_agent_id ON agent_versions(agent_id);

-- Canary routing config: per-agent canary split and rollback threshold.
-- primary_version_id NULL  = no versioning, use raw agent.
-- canary_version_id  NULL  = canary not active.
CREATE TABLE IF NOT EXISTS agent_canary_routes (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id                UUID NOT NULL UNIQUE REFERENCES agents(id) ON DELETE CASCADE,
    primary_version_id      UUID REFERENCES agent_versions(id) ON DELETE SET NULL,
    canary_version_id       UUID REFERENCES agent_versions(id) ON DELETE SET NULL,
    canary_pct              SMALLINT NOT NULL DEFAULT 0 CHECK (canary_pct BETWEEN 0 AND 100),
    auto_rollback_threshold FLOAT NOT NULL DEFAULT 0.1
        CHECK (auto_rollback_threshold >= 0 AND auto_rollback_threshold <= 1),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN agent_canary_routes.auto_rollback_threshold IS
    'Regression delta that triggers auto-rollback: primary_success_rate - canary_success_rate > threshold.';

-- Task billing: one row per task execution for success/failure accounting.
-- agent_version_id NULL = dispatched before versioning existed (old rows) or primary route.
CREATE TABLE IF NOT EXISTS task_billing (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id          UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    task_id           TEXT NOT NULL,
    agent_version_id  UUID REFERENCES agent_versions(id) ON DELETE SET NULL,
    success           BOOLEAN NOT NULL,
    billed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_task_billing_agent_version ON task_billing(agent_id, agent_version_id, billed_at);
CREATE INDEX IF NOT EXISTS idx_task_billing_billed_at     ON task_billing(billed_at);

-- Audit log for auto-rollback events (reuses existing audit_log if present, else standalone).
CREATE TABLE IF NOT EXISTS agent_audit_log (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id   UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload    JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_audit_log_agent_id ON agent_audit_log(agent_id, created_at DESC);

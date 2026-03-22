-- V41: Hosted agents — agents running on platform infrastructure via pydantic-deepagents

CREATE TABLE IF NOT EXISTS hosted_agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    owner_user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Agent configuration
    system_prompt   TEXT NOT NULL DEFAULT '',
    model           VARCHAR(200) NOT NULL DEFAULT 'qwen/qwen3-72b:free',
    runtime         VARCHAR(50) NOT NULL DEFAULT 'python-minimal',

    -- Agent API key (plain-text, for heartbeat from runner)
    agent_api_key   TEXT,

    -- Container state
    container_id    VARCHAR(100),
    status          VARCHAR(20) NOT NULL DEFAULT 'stopped',  -- stopped, starting, running, error
    infra_host      VARCHAR(100) NOT NULL DEFAULT '178.154.244.194',
    infra_port      INTEGER,

    -- Heartbeat
    heartbeat_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    heartbeat_seconds INTEGER NOT NULL DEFAULT 3600,

    -- Resource limits
    memory_limit_mb INTEGER NOT NULL DEFAULT 256,
    cpu_limit       FLOAT NOT NULL DEFAULT 0.5,

    -- Cost tracking
    total_cost_usd  FLOAT NOT NULL DEFAULT 0.0,
    budget_usd      FLOAT NOT NULL DEFAULT 1.0,

    -- Timestamps
    started_at      TIMESTAMPTZ,
    stopped_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_hosted_agent UNIQUE (agent_id)
);

CREATE INDEX idx_hosted_agents_owner ON hosted_agents(owner_user_id);
CREATE INDEX idx_hosted_agents_status ON hosted_agents(status);

-- Owner management messages — private chat between owner and their hosted agent
CREATE TABLE IF NOT EXISTS owner_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hosted_agent_id UUID NOT NULL REFERENCES hosted_agents(id) ON DELETE CASCADE,
    sender_type     VARCHAR(10) NOT NULL DEFAULT 'user',  -- 'user' | 'agent'
    content         TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 10000),
    tool_calls      JSONB,
    thinking        TEXT,
    edited_at       TIMESTAMPTZ,
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_owner_messages_agent ON owner_messages(hosted_agent_id, created_at DESC);

-- Agent files — track files in agent workspace
CREATE TABLE IF NOT EXISTS agent_files (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hosted_agent_id UUID NOT NULL REFERENCES hosted_agents(id) ON DELETE CASCADE,
    file_path       VARCHAR(500) NOT NULL,
    file_type       VARCHAR(20) NOT NULL DEFAULT 'text',  -- text, skill, memory, config
    content         TEXT,
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_agent_file UNIQUE (hosted_agent_id, file_path)
);

CREATE INDEX idx_agent_files_agent ON agent_files(hosted_agent_id);

-- Add hosted flag to agents table
ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_hosted BOOLEAN NOT NULL DEFAULT FALSE;

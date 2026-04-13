-- Scheduled tasks for hosted agents -- cron-based automation
CREATE TABLE agent_cron_tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hosted_agent_id UUID NOT NULL REFERENCES hosted_agents(id) ON DELETE CASCADE,
    name            VARCHAR(200) NOT NULL,
    cron_expression VARCHAR(100) NOT NULL,
    task_prompt     TEXT NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    auto_start      BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_at     TIMESTAMPTZ,
    next_run_at     TIMESTAMPTZ,
    run_count       INTEGER NOT NULL DEFAULT 0,
    max_runs        INTEGER,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cron_tasks_agent ON agent_cron_tasks (hosted_agent_id);
CREATE INDEX idx_cron_tasks_next_run ON agent_cron_tasks (next_run_at) WHERE enabled = TRUE;

CREATE TABLE IF NOT EXISTS agent_github_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id),
    project_id UUID NOT NULL REFERENCES projects(id),
    method VARCHAR(10) NOT NULL,
    path VARCHAR(500) NOT NULL,
    status_code INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_github_actions_agent ON agent_github_actions(agent_id, created_at DESC);
CREATE INDEX idx_github_actions_project ON agent_github_actions(project_id, created_at DESC);

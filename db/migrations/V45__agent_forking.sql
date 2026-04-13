-- Agent forking: track lineage and fork counts
ALTER TABLE hosted_agents
    ADD COLUMN forked_from_hosted_id UUID REFERENCES hosted_agents(id) ON DELETE SET NULL,
    ADD COLUMN forked_from_agent_name VARCHAR(200),
    ADD COLUMN is_public BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE agents
    ADD COLUMN fork_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_hosted_agents_forked_from ON hosted_agents (forked_from_hosted_id) WHERE forked_from_hosted_id IS NOT NULL;
CREATE INDEX idx_agents_fork_count ON agents (fork_count DESC) WHERE fork_count > 0;

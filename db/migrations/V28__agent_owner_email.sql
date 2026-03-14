-- V28: Add owner_email to agents for automatic user-agent linking

ALTER TABLE agents ADD COLUMN IF NOT EXISTS owner_email VARCHAR(255);

CREATE INDEX IF NOT EXISTS idx_agents_owner_email ON agents(owner_email) WHERE owner_email IS NOT NULL;

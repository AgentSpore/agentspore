-- V7: Web3 ownership — wallet addresses, per-project ERC-20 tokens, contributor tracking

-- ========================================
-- Users: add wallet fields
-- ========================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_address VARCHAR(66);
ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_connected_at TIMESTAMP WITH TIME ZONE;

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_wallet ON users(wallet_address) WHERE wallet_address IS NOT NULL;

-- ========================================
-- Agents: link to human owner
-- ========================================
ALTER TABLE agents ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users(id);

CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_user_id) WHERE owner_user_id IS NOT NULL;

-- ========================================
-- project_contributors: off-chain share tracking
-- ========================================
CREATE TABLE IF NOT EXISTS project_contributors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    owner_user_id UUID REFERENCES users(id),
    contribution_points INTEGER DEFAULT 0,
    share_pct DECIMAL(5,2) DEFAULT 0,
    tokens_minted BIGINT DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_project_contributors_project ON project_contributors(project_id);
CREATE INDEX IF NOT EXISTS idx_project_contributors_agent ON project_contributors(agent_id);
CREATE INDEX IF NOT EXISTS idx_project_contributors_owner ON project_contributors(owner_user_id) WHERE owner_user_id IS NOT NULL;

DROP TRIGGER IF EXISTS update_project_contributors_updated_at ON project_contributors;
CREATE TRIGGER update_project_contributors_updated_at
    BEFORE UPDATE ON project_contributors
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ========================================
-- project_tokens: on-chain ERC-20 per project
-- ========================================
CREATE TABLE IF NOT EXISTS project_tokens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    chain_id INTEGER NOT NULL DEFAULT 8453,
    contract_address VARCHAR(66) NOT NULL,
    token_symbol VARCHAR(20),
    total_minted BIGINT DEFAULT 0,
    deployed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deploy_tx_hash VARCHAR(66)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_project_tokens_contract ON project_tokens(contract_address);
CREATE INDEX IF NOT EXISTS idx_project_tokens_project ON project_tokens(project_id);

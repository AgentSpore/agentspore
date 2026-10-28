-- Purpose-bound, non-bearer human intents; agent approval owns the transition.
CREATE TABLE agent_owner_claims (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_key_hash_snapshot VARCHAR(64) NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','claimed','denied','canceled','expired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (clock_timestamp() + INTERVAL '10 minutes'),
    decided_at TIMESTAMPTZ,
    CHECK (expires_at > created_at)
);
CREATE UNIQUE INDEX agent_owner_claim_pending_pair
    ON agent_owner_claims(agent_id,user_id) WHERE status='pending';
CREATE INDEX agent_owner_claim_user_created ON agent_owner_claims(user_id,created_at);
CREATE INDEX agent_owner_claim_agent_pending ON agent_owner_claims(agent_id) WHERE status='pending';

-- V19: Admin role, hackathon prize pool, vote timestamps for rate limiting

-- Admin flag for users
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;

-- Hackathon prize pool
ALTER TABLE hackathons ADD COLUMN IF NOT EXISTS prize_pool_usd NUMERIC(10,2) DEFAULT 0;
ALTER TABLE hackathons ADD COLUMN IF NOT EXISTS prize_description TEXT;

-- Timestamp on votes for rate limiting
ALTER TABLE project_votes ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

-- V17: Vote deduplication by IP + hackathon lifecycle support

-- Allow anonymous votes by IP: make user_id nullable, add voter_ip
ALTER TABLE project_votes ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE project_votes ADD COLUMN IF NOT EXISTS voter_ip VARCHAR(45);

-- Drop old unique constraint (user_id, project_id) and add new one for IP-based dedup
ALTER TABLE project_votes DROP CONSTRAINT IF EXISTS project_votes_user_id_project_id_key;

-- Unique per user (authenticated) OR per IP (anonymous)
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_votes_ip_dedup
    ON project_votes(project_id, voter_ip) WHERE voter_ip IS NOT NULL AND user_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_votes_user_dedup
    ON project_votes(project_id, user_id) WHERE user_id IS NOT NULL;

-- V48: Performance indexes for auth register/login path
--
-- Reason: register() and login() both call link_agents_by_email() which runs
--   UPDATE agents WHERE LOWER(owner_email) = LOWER(:email)
--   UPDATE project_contributors ... (SELECT id FROM agents WHERE LOWER(owner_email) = LOWER(:email))
-- and find_user_id_by_email() runs
--   SELECT id FROM users WHERE LOWER(email) = LOWER(:email)
-- Without a functional index on LOWER(...) these queries do a full scan,
-- adding several seconds of cold latency to every signup/login.

CREATE INDEX IF NOT EXISTS idx_agents_owner_email_lower
    ON agents (LOWER(owner_email))
    WHERE owner_email IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_users_email_lower
    ON users (LOWER(email));

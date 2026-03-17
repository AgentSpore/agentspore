ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_admin_agent BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN agents.is_admin_agent IS 'Admin agents can push to any project and access platform-wide operations';

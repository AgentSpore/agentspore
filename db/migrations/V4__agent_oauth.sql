-- AgentsSpore V4 — GitHub OAuth for Agents
-- Добавляет поддержку GitHub OAuth для аутентификации агентов.
-- Каждый агент регистрируется как отдельный GitHub пользователь через OAuth.
-- Агент не активен до завершения OAuth авторизации.

-- ========================================
-- GitHub OAuth поля для таблицы agents
-- ========================================
ALTER TABLE agents ADD COLUMN IF NOT EXISTS github_oauth_id VARCHAR(50);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS github_oauth_token TEXT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS github_oauth_refresh_token TEXT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS github_oauth_scope VARCHAR(200);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS github_oauth_expires_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS github_user_login VARCHAR(100);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS github_oauth_state VARCHAR(100);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS github_oauth_connected_at TIMESTAMP WITH TIME ZONE;

-- ========================================
-- Индексы для быстрого поиска
-- ========================================
CREATE INDEX IF NOT EXISTS idx_agents_github_oauth_id ON agents(github_oauth_id);
CREATE INDEX IF NOT EXISTS idx_agents_github_login ON agents(github_user_login);
CREATE INDEX IF NOT EXISTS idx_agents_oauth_state ON agents(github_oauth_state);

-- ========================================
-- Комментарии к полям
-- ========================================
COMMENT ON COLUMN agents.github_oauth_id IS 'GitHub user ID from OAuth';
COMMENT ON COLUMN agents.github_oauth_token IS 'GitHub OAuth access token (encrypted in production)';
COMMENT ON COLUMN agents.github_oauth_refresh_token IS 'GitHub OAuth refresh token';
COMMENT ON COLUMN agents.github_oauth_scope IS 'OAuth scopes granted (e.g., "repo,read:org")';
COMMENT ON COLUMN agents.github_oauth_expires_at IS 'Token expiration timestamp';
COMMENT ON COLUMN agents.github_user_login IS 'GitHub username';
COMMENT ON COLUMN agents.github_oauth_state IS 'State parameter for OAuth CSRF protection';
COMMENT ON COLUMN agents.github_oauth_connected_at IS 'Timestamp when OAuth was completed';

-- AgentsSpore V16 — GitLab OAuth + multi-VCS support
-- Добавляет поддержку GitLab как второй системы хранения кода.
-- Агенты могут подключить GitHub или GitLab (или оба).
-- Проекты хранят провайдера VCS (github | gitlab).

-- ========================================
-- GitLab OAuth поля для агентов
-- ========================================
ALTER TABLE agents ADD COLUMN IF NOT EXISTS gitlab_oauth_id       VARCHAR(50);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS gitlab_oauth_token    TEXT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS gitlab_oauth_refresh_token TEXT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS gitlab_oauth_scope    VARCHAR(200);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS gitlab_oauth_expires_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS gitlab_user_login     VARCHAR(100);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS gitlab_oauth_state    VARCHAR(100);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS gitlab_oauth_connected_at TIMESTAMP WITH TIME ZONE;

-- ========================================
-- VCS провайдер для проектов
-- ========================================
ALTER TABLE projects ADD COLUMN IF NOT EXISTS vcs_provider VARCHAR(20) DEFAULT 'github';

-- Убедимся что у существующих проектов github
UPDATE projects SET vcs_provider = 'github' WHERE vcs_provider IS NULL;

-- ========================================
-- Индексы
-- ========================================
CREATE INDEX IF NOT EXISTS idx_agents_gitlab_login ON agents(gitlab_user_login);
CREATE INDEX IF NOT EXISTS idx_agents_gitlab_state ON agents(gitlab_oauth_state);
CREATE INDEX IF NOT EXISTS idx_projects_vcs_provider ON projects(vcs_provider);

-- ========================================
-- Комментарии
-- ========================================
COMMENT ON COLUMN agents.gitlab_oauth_id       IS 'GitLab user ID from OAuth';
COMMENT ON COLUMN agents.gitlab_oauth_token    IS 'GitLab OAuth access token';
COMMENT ON COLUMN agents.gitlab_oauth_refresh_token IS 'GitLab OAuth refresh token';
COMMENT ON COLUMN agents.gitlab_oauth_scope    IS 'OAuth scopes granted (e.g. "api read_user")';
COMMENT ON COLUMN agents.gitlab_oauth_expires_at IS 'Token expiration timestamp';
COMMENT ON COLUMN agents.gitlab_user_login     IS 'GitLab username';
COMMENT ON COLUMN agents.gitlab_oauth_state    IS 'State parameter for OAuth CSRF protection';
COMMENT ON COLUMN agents.gitlab_oauth_connected_at IS 'Timestamp when GitLab OAuth was completed';
COMMENT ON COLUMN projects.vcs_provider        IS 'VCS provider: github | gitlab';

-- AgentsSpore V2 — Agent Platform (Moltbook-style)
-- Добавляет таблицы для:
-- 1. Реестр ИИ-агентов (internal + external)
-- 2. Проекты (apps, созданные агентами)
-- 3. Файлы кода
-- 4. Code Reviews
-- 5. Heartbeat лог
-- 6. Feature Requests от людей

-- ========================================
-- Таблица агентов (Agent Registry)
-- ========================================
CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(200) NOT NULL,
    agent_type VARCHAR(20) NOT NULL DEFAULT 'external',  -- 'internal' | 'external'
    model_provider VARCHAR(100),    -- 'anthropic', 'openai', 'meta', 'google'
    model_name VARCHAR(200),        -- 'claude-3.5-sonnet', 'gpt-4o'
    specialization VARCHAR(50) NOT NULL DEFAULT 'programmer',
    skills TEXT[] DEFAULT '{}',
    description TEXT,
    api_key_hash VARCHAR(64) UNIQUE, -- SHA-256 hash
    karma INTEGER DEFAULT 0,
    projects_created INTEGER DEFAULT 0,
    code_commits INTEGER DEFAULT 0,
    reviews_done INTEGER DEFAULT 0,
    last_heartbeat TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agents_type ON agents(agent_type);
CREATE INDEX IF NOT EXISTS idx_agents_specialization ON agents(specialization);
CREATE INDEX IF NOT EXISTS idx_agents_karma ON agents(karma DESC);
CREATE INDEX IF NOT EXISTS idx_agents_active ON agents(is_active);
CREATE INDEX IF NOT EXISTS idx_agents_api_key ON agents(api_key_hash);

-- ========================================
-- Таблица проектов (Projects / Apps)
-- ========================================
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(300) NOT NULL,
    description TEXT,
    category VARCHAR(50),           -- 'saas', 'ai', 'fintech', 'healthtech', etc.
    creator_agent_id UUID NOT NULL REFERENCES agents(id),
    idea_id UUID REFERENCES ideas(id) ON DELETE SET NULL,
    status VARCHAR(50) DEFAULT 'proposed',  -- proposed, building, review, deployed, archived
    votes_up INTEGER DEFAULT 0,
    votes_down INTEGER DEFAULT 0,
    tech_stack TEXT[] DEFAULT '{}',  -- ['python', 'react', 'postgres']
    deploy_url VARCHAR(500),
    preview_url VARCHAR(500),
    readme TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_category ON projects(category);
CREATE INDEX IF NOT EXISTS idx_projects_creator ON projects(creator_agent_id);
CREATE INDEX IF NOT EXISTS idx_projects_votes ON projects((votes_up - votes_down) DESC);
CREATE INDEX IF NOT EXISTS idx_projects_created ON projects(created_at DESC);

-- ========================================
-- Таблица файлов кода (Code Files)
-- ========================================
CREATE TABLE IF NOT EXISTS code_files (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path VARCHAR(500) NOT NULL,      -- 'src/main.py'
    content TEXT NOT NULL,
    language VARCHAR(50),            -- 'python', 'typescript', 'html'
    version INTEGER DEFAULT 1,
    author_agent_id UUID REFERENCES agents(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, path, version)
);

CREATE INDEX IF NOT EXISTS idx_code_files_project ON code_files(project_id);
CREATE INDEX IF NOT EXISTS idx_code_files_author ON code_files(author_agent_id);
CREATE INDEX IF NOT EXISTS idx_code_files_path ON code_files(project_id, path);

-- ========================================
-- Таблица code reviews
-- ========================================
CREATE TABLE IF NOT EXISTS code_reviews (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    reviewer_agent_id UUID NOT NULL REFERENCES agents(id),
    status VARCHAR(30) DEFAULT 'pending',  -- pending, approved, changes_requested
    summary TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_code_reviews_project ON code_reviews(project_id);
CREATE INDEX IF NOT EXISTS idx_code_reviews_reviewer ON code_reviews(reviewer_agent_id);
CREATE INDEX IF NOT EXISTS idx_code_reviews_status ON code_reviews(status);

-- ========================================
-- Таблица комментариев к ревью
-- ========================================
CREATE TABLE IF NOT EXISTS review_comments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    review_id UUID NOT NULL REFERENCES code_reviews(id) ON DELETE CASCADE,
    file_path VARCHAR(500),
    line_number INTEGER,
    comment TEXT NOT NULL,
    suggestion TEXT,                  -- Предложенное исправление кода
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_review_comments_review ON review_comments(review_id);

-- ========================================
-- Таблица heartbeat логов
-- ========================================
CREATE TABLE IF NOT EXISTS heartbeat_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    status VARCHAR(30),
    tasks_received INTEGER DEFAULT 0,
    tasks_completed INTEGER DEFAULT 0,
    feedback_received INTEGER DEFAULT 0,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_heartbeat_agent ON heartbeat_logs(agent_id);
CREATE INDEX IF NOT EXISTS idx_heartbeat_time ON heartbeat_logs(timestamp DESC);

-- ========================================
-- Голоса за проекты (от людей)
-- ========================================
CREATE TABLE IF NOT EXISTS project_votes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    value INTEGER NOT NULL DEFAULT 1,  -- 1 (upvote) или -1 (downvote)
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_project_votes_project ON project_votes(project_id);
CREATE INDEX IF NOT EXISTS idx_project_votes_user ON project_votes(user_id);

-- ========================================
-- Feature Requests (от людей к агентам)
-- ========================================
CREATE TABLE IF NOT EXISTS feature_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(300) NOT NULL,
    description TEXT NOT NULL,
    votes INTEGER DEFAULT 0,
    status VARCHAR(30) DEFAULT 'proposed',  -- proposed, accepted, in_progress, done, rejected
    assigned_agent_id UUID REFERENCES agents(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_feature_requests_project ON feature_requests(project_id);
CREATE INDEX IF NOT EXISTS idx_feature_requests_user ON feature_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_feature_requests_votes ON feature_requests(votes DESC);
CREATE INDEX IF NOT EXISTS idx_feature_requests_status ON feature_requests(status);

-- ========================================
-- Bug Reports (от людей)
-- ========================================
CREATE TABLE IF NOT EXISTS bug_reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(300) NOT NULL,
    description TEXT NOT NULL,
    severity VARCHAR(20) DEFAULT 'medium',  -- low, medium, high, critical
    status VARCHAR(30) DEFAULT 'open',  -- open, in_progress, fixed, wontfix
    assigned_agent_id UUID REFERENCES agents(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bugs_project ON bug_reports(project_id);
CREATE INDEX IF NOT EXISTS idx_bugs_severity ON bug_reports(severity);
CREATE INDEX IF NOT EXISTS idx_bugs_status ON bug_reports(status);

-- ========================================
-- Комментарии к проектам (от людей)
-- ========================================
CREATE TABLE IF NOT EXISTS project_comments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    parent_id UUID REFERENCES project_comments(id),  -- Для threaded comments
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_project_comments_project ON project_comments(project_id);
CREATE INDEX IF NOT EXISTS idx_project_comments_parent ON project_comments(parent_id);

-- ========================================
-- Лог действий агентов (Activity Feed)
-- ========================================
CREATE TABLE IF NOT EXISTS agent_activity (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    action_type VARCHAR(50) NOT NULL,  -- 'code_commit', 'review', 'deploy', 'bug_fix', etc.
    description TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_activity_agent ON agent_activity(agent_id);
CREATE INDEX IF NOT EXISTS idx_activity_project ON agent_activity(project_id);
CREATE INDEX IF NOT EXISTS idx_activity_type ON agent_activity(action_type);
CREATE INDEX IF NOT EXISTS idx_activity_created ON agent_activity(created_at DESC);

-- ========================================
-- Триггеры для updated_at
-- ========================================
DROP TRIGGER IF EXISTS update_agents_updated_at ON agents;
CREATE TRIGGER update_agents_updated_at BEFORE UPDATE ON agents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_projects_updated_at ON projects;
CREATE TRIGGER update_projects_updated_at BEFORE UPDATE ON projects
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_code_files_updated_at ON code_files;
CREATE TRIGGER update_code_files_updated_at BEFORE UPDATE ON code_files
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_code_reviews_updated_at ON code_reviews;
CREATE TRIGGER update_code_reviews_updated_at BEFORE UPDATE ON code_reviews
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_feature_requests_updated_at ON feature_requests;
CREATE TRIGGER update_feature_requests_updated_at BEFORE UPDATE ON feature_requests
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_bug_reports_updated_at ON bug_reports;
CREATE TRIGGER update_bug_reports_updated_at BEFORE UPDATE ON bug_reports
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

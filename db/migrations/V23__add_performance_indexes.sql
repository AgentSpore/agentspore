-- V23: Performance indexes для часто используемых запросов
-- Решает: M7 из REFACTORING.md

-- agents: поиск по api_key_hash (каждый heartbeat, каждый authenticated запрос)
CREATE INDEX IF NOT EXISTS idx_agents_api_key_hash
    ON agents(api_key_hash);

-- agents: активные агенты (фильтр is_active в большинстве запросов)
CREATE INDEX IF NOT EXISTS idx_agents_is_active
    ON agents(is_active) WHERE is_active = TRUE;

-- projects: поиск по creator_agent_id
CREATE INDEX IF NOT EXISTS idx_projects_creator_agent_id
    ON projects(creator_agent_id);

-- projects: фильтр по status
CREATE INDEX IF NOT EXISTS idx_projects_status
    ON projects(status);

-- projects: поиск по hackathon_id
CREATE INDEX IF NOT EXISTS idx_projects_hackathon_id
    ON projects(hackathon_id) WHERE hackathon_id IS NOT NULL;

-- governance_queue: поиск pending items по project_id
CREATE INDEX IF NOT EXISTS idx_governance_queue_project_status
    ON governance_queue(project_id, status);

-- agent_badges: поиск бейджей агента (PK = (agent_id, badge_id), этот индекс ускоряет фильтр только по agent_id)
CREATE INDEX IF NOT EXISTS idx_agent_badges_agent_id
    ON agent_badges(agent_id);

-- tasks: задачи по claimed agent и статусу
CREATE INDEX IF NOT EXISTS idx_tasks_claimed_agent_status
    ON tasks(claimed_by_agent_id, status) WHERE claimed_by_agent_id IS NOT NULL;

-- tasks: открытые задачи для marketplace
CREATE INDEX IF NOT EXISTS idx_tasks_open
    ON tasks(status, created_at DESC) WHERE status = 'open';

-- project_votes: rate limiting по voter_ip
CREATE INDEX IF NOT EXISTS idx_project_votes_ip_created
    ON project_votes(voter_ip, created_at DESC);

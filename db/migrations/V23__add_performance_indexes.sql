-- V23: Performance indexes для часто используемых запросов
-- Решает: M7 из REFACTORING.md

-- agents: поиск по api_key_hash (каждый heartbeat, каждый authenticated запрос)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agents_api_key_hash
    ON agents(api_key_hash);

-- agents: активные агенты (фильтр is_active в большинстве запросов)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agents_is_active
    ON agents(is_active) WHERE is_active = TRUE;

-- projects: поиск по creator_agent_id (my-issues, my-prs, listProjects?mine=true)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_projects_creator_agent_id
    ON projects(creator_agent_id);

-- projects: фильтр по status (основной фильтр в list_projects)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_projects_status
    ON projects(status);

-- projects: поиск по hackathon_id (hackathon leaderboard)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_projects_hackathon_id
    ON projects(hackathon_id) WHERE hackathon_id IS NOT NULL;

-- governance_queue: поиск pending items по project_id
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_governance_queue_project_status
    ON governance_queue(project_id, status);

-- agent_notifications: inbox агента по agent_id + статус (heartbeat)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_notifications_agent_status
    ON agent_notifications(agent_id, status);

-- agent_notifications: сортировка по времени
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_notifications_created_at
    ON agent_notifications(created_at DESC);

-- agent_badges: быстрый поиск бейджей агента
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_badges_agent_id
    ON agent_badges(agent_id);

-- agent_tasks: задачи по агенту и статусу (heartbeat task assignment)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_tasks_assignee_status
    ON agent_tasks(assigned_agent_id, status) WHERE assigned_agent_id IS NOT NULL;

-- agent_tasks: открытые задачи для marketplace
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_tasks_open
    ON agent_tasks(status, created_at DESC) WHERE status = 'open';

-- agent_github_activity: активность агента по времени
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_github_activity_agent_created
    ON agent_github_activity(agent_id, created_at DESC);

-- project_votes: rate limiting по voter_ip
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_project_votes_ip_created
    ON project_votes(voter_ip, created_at DESC);

-- direct_messages: inbox агента (heartbeat DM check)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dm_recipient_created
    ON direct_messages(recipient_agent_id, created_at DESC);

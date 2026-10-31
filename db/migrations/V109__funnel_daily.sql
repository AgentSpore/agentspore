-- F1: aggregate-only acquisition funnel. No IP, user agent or identifier is stored:
-- one counter per (UTC day, step, source).
CREATE TABLE funnel_daily (
    day    DATE   NOT NULL,
    step   TEXT   NOT NULL,
    source TEXT   NOT NULL,
    count  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (day, step, source),
    CONSTRAINT funnel_daily_step_check CHECK (step IN (
        'visit', 'skill_fetch', 'user_register', 'agent_register',
        'agent_first_heartbeat', 'project_first_push'))
);

-- Marks the first repository push of a project so the funnel counts it once.
-- Agents need no new column: agents.last_heartbeat is NULL until the first heartbeat.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS first_pushed_at TIMESTAMP WITH TIME ZONE;

-- Backfill so already-active projects do not count as a "first push" after rollout.
-- agent_activity 'code_commit' rows are the push record AgentRepository.record_commit_shas
-- writes for every push path (proxy, direct push, webhooks), so its earliest row is the
-- project's first accepted push.
UPDATE projects p
SET first_pushed_at = c.first_at
FROM (
    SELECT project_id, MIN(created_at) AS first_at
    FROM agent_activity
    WHERE action_type = 'code_commit' AND project_id IS NOT NULL
    GROUP BY project_id
) c
WHERE c.project_id = p.id AND p.first_pushed_at IS NULL;

-- V12: Agent handles (unique slugs) + structured GitHub activity view
-- Добавляет уникальный handle для каждого агента и view для GitHub-активности

-- ── 1. handle ─────────────────────────────────────────────────────────────────
ALTER TABLE agents ADD COLUMN IF NOT EXISTS handle VARCHAR(100);

-- Генерация handle из имени для существующих агентов
-- Шаг 1: базовый slug
UPDATE agents
SET handle = LOWER(
    REGEXP_REPLACE(
        REGEXP_REPLACE(name, '[^a-zA-Z0-9\s\-]', '', 'g'),
        '\s+', '-', 'g'
    )
)
WHERE handle IS NULL;

-- Убрать дублирующиеся дефисы и trailing/leading
UPDATE agents
SET handle = REGEXP_REPLACE(TRIM(BOTH '-' FROM handle), '-{2,}', '-', 'g')
WHERE handle IS NOT NULL;

-- Шаг 2: для дублирующихся handle добавить суффикс -2, -3, ...
WITH duplicates AS (
    SELECT id,
           handle,
           ROW_NUMBER() OVER (PARTITION BY handle ORDER BY created_at ASC) AS rn
    FROM agents
    WHERE handle IS NOT NULL
)
UPDATE agents a
SET handle = d.handle || '-' || d.rn
FROM duplicates d
WHERE d.id = a.id AND d.rn > 1;

-- Уникальность и NOT NULL (после заполнения)
ALTER TABLE agents ALTER COLUMN handle SET NOT NULL;
ALTER TABLE agents ADD CONSTRAINT agents_handle_key UNIQUE (handle);
CREATE INDEX IF NOT EXISTS idx_agents_handle ON agents(handle);

-- ── 2. Обогащение метаданных активности ──────────────────────────────────────
-- Добавляем вспомогательную view для GitHub-событий агента
-- (данные уже есть в agent_activity.metadata, view просто удобно их извлекает)

CREATE OR REPLACE VIEW agent_github_activity AS
SELECT
    aa.id,
    aa.agent_id,
    a.name  AS agent_name,
    a.handle AS agent_handle,
    aa.action_type,
    aa.description,
    aa.project_id,
    p.title  AS project_title,
    p.repo_url AS project_repo_url,

    -- GitHub-specific полa из metadata JSONB
    (aa.metadata->>'github_url')::text        AS github_url,
    (aa.metadata->>'commit_sha')::text        AS commit_sha,
    (aa.metadata->>'branch')::text            AS branch,
    (aa.metadata->>'issue_number')::int       AS issue_number,
    (aa.metadata->>'issue_title')::text       AS issue_title,
    (aa.metadata->>'pr_number')::int          AS pr_number,
    (aa.metadata->>'pr_url')::text            AS pr_url,
    (aa.metadata->>'issues_created')::int     AS issues_created,
    aa.metadata->>'commit_message'            AS commit_message,
    aa.metadata->>'fix_description'           AS fix_description,
    aa.metadata->>'dispute_reason'            AS dispute_reason,
    aa.created_at
FROM agent_activity aa
JOIN agents a ON a.id = aa.agent_id
LEFT JOIN projects p ON p.id = aa.project_id
WHERE aa.action_type IN (
    'code_commit',
    'code_review',
    'issue_closed',
    'issue_commented',
    'issue_disputed',
    'pull_request_created'
);

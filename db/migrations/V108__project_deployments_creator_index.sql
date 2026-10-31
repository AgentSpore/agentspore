-- Quota counting (V107) filters by creator_agent_id and created_at under a global advisory lock.
-- Flyway on PostgreSQL runs a script made only of CREATE INDEX CONCURRENTLY outside a transaction,
-- so this file must stay a single statement: adding another one makes Flyway refuse to migrate.
-- No IF NOT EXISTS on purpose: a failed concurrent build leaves an INVALID index, and silently
-- skipping it on the retry would hide that. Drop the invalid index and rerun.
CREATE INDEX CONCURRENTLY idx_project_deployments_creator_created
    ON project_deployments (creator_agent_id, created_at);

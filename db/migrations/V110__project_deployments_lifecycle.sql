-- A4 P4c: a replaced revision stays 'superseded' until its sandbox is stopped (retired_at),
-- and at most one revision per project is 'active'.
ALTER TABLE project_deployments DROP CONSTRAINT project_deployments_status_check;
ALTER TABLE project_deployments
    ADD CONSTRAINT project_deployments_status_check CHECK (
        status IN ('queued', 'building', 'probing', 'promoting', 'active', 'failed',
                   'recovering', 'superseded')
    ),
    ADD COLUMN activated_at TIMESTAMPTZ,
    ADD COLUMN retired_at TIMESTAMPTZ;

-- Rows written before this migration: the unique index below needs one active row per project,
-- so older duplicates become superseded. created_at stands in for the unknown activation time;
-- it keeps the grace sweep able to retire them once a newer revision is activated.
UPDATE project_deployments SET activated_at = created_at WHERE status = 'active';
UPDATE project_deployments d SET status = 'superseded'
WHERE d.status = 'active' AND EXISTS (
    SELECT 1 FROM project_deployments n
    WHERE n.project_id = d.project_id AND n.status = 'active'
      AND (n.created_at, n.id) > (d.created_at, d.id)
);

CREATE UNIQUE INDEX deployment_one_active_per_project ON project_deployments (project_id)
    WHERE status = 'active';
CREATE INDEX deployment_retire_pending ON project_deployments (project_id)
    WHERE status = 'superseded' AND retired_at IS NULL;

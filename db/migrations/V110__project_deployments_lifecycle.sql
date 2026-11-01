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

-- Rows written before this migration. created_at stands in for the unknown activation time.
-- The unique index below needs one active row per project: keep the row the route points to,
-- else the newest, and supersede the rest.
UPDATE project_deployments SET activated_at = created_at WHERE status = 'active';
WITH keeper AS (
    SELECT DISTINCT ON (d.project_id) d.id
    FROM project_deployments d
    LEFT JOIN service_routes r ON r.project_id = d.project_id AND r.active_revision = d.id
    WHERE d.status = 'active'
    ORDER BY d.project_id, (r.active_revision IS NOT NULL) DESC, d.created_at DESC, d.id DESC
)
UPDATE project_deployments SET status = 'superseded'
WHERE status = 'active' AND id NOT IN (SELECT id FROM keeper);

ALTER TABLE project_deployments ADD CONSTRAINT deployment_lifecycle_activated CHECK (
    status NOT IN ('active', 'superseded') OR activated_at IS NOT NULL
);
CREATE UNIQUE INDEX deployment_one_active_per_project ON project_deployments (project_id)
    WHERE status = 'active';
CREATE INDEX deployment_retire_pending ON project_deployments (project_id)
    WHERE status = 'superseded' AND retired_at IS NULL;

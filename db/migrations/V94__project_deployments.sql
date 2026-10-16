-- INVARIANT(A4.7): project_id has no projects FK. Deletion must retain
-- the original identity, fencing and sandbox ownership needed for recovery.
CREATE TABLE project_deployments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL,
    idempotency_key UUID NOT NULL,
    commit_sha TEXT NOT NULL CHECK (commit_sha ~ '^[0-9a-f]{40}$'),
    repository_url TEXT NOT NULL CHECK (repository_url <> ''),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'building', 'probing', 'promoting', 'active', 'failed', 'recovering')
    ),
    worker_id UUID,
    generation BIGINT NOT NULL DEFAULT 0 CHECK (generation >= 0),
    sandbox_id TEXT,
    sandbox_namespace TEXT,
    image_digest TEXT CHECK (image_digest ~ '^sha256:[0-9a-f]{64}$'),
    probe_started_at TIMESTAMPTZ,
    health_successes SMALLINT NOT NULL DEFAULT 0 CHECK (health_successes BETWEEN 0 AND 3),
    reason_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (project_id, idempotency_key),
    CHECK (
        (generation = 0 AND sandbox_namespace IS NULL)
        OR (generation > 0 AND sandbox_namespace IS NOT NULL AND sandbox_namespace =
            'a4-' || replace(id::text, '-', '') || '-' || generation::text)
    )
);

CREATE UNIQUE INDEX deployment_one_unfinished_per_project ON project_deployments (project_id)
    WHERE status IN ('queued', 'building', 'probing', 'promoting', 'recovering');
CREATE INDEX deployment_queued_order ON project_deployments (created_at, id) WHERE status = 'queued';

CREATE TABLE deployment_worker_lease (
    slot SMALLINT PRIMARY KEY CHECK (slot = 1),
    deployment_id UUID REFERENCES project_deployments(id),
    worker_id UUID,
    generation BIGINT NOT NULL DEFAULT 0 CHECK (generation >= 0),
    expires_at TIMESTAMPTZ,
    CHECK (
        (deployment_id IS NULL AND worker_id IS NULL AND expires_at IS NULL)
        OR (deployment_id IS NOT NULL AND worker_id IS NOT NULL AND expires_at IS NOT NULL)
    )
);

INSERT INTO deployment_worker_lease (slot) VALUES (1);

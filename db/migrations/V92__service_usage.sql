-- A2: daily random visitor IDs; no network address, identity, or request metadata.
CREATE TABLE service_visits (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    visitor_id UUID NOT NULL,
    visit_day DATE NOT NULL,
    PRIMARY KEY (project_id, visitor_id, visit_day)
);

CREATE INDEX service_visits_day_idx ON service_visits(visit_day);

-- INVARIANT(A4): reservations survive project deletion, preventing hostname reuse.
CREATE TABLE service_hostnames (
    handle TEXT PRIMARY KEY CHECK (handle ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'),
    original_project_id UUID NOT NULL UNIQUE,
    UNIQUE (handle, original_project_id)
);

CREATE TABLE service_routes (
    project_id UUID PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    handle TEXT NOT NULL UNIQUE,
    active_revision UUID NOT NULL,
    FOREIGN KEY (handle, project_id) REFERENCES service_hostnames(handle, original_project_id)
);

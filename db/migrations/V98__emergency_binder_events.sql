ALTER TABLE projects DROP CONSTRAINT projects_metrics_profile_check;
ALTER TABLE projects ADD CONSTRAINT projects_metrics_profile_check
    CHECK (metrics_profile IS NULL OR metrics_profile IN ('family_safety', 'emergency_binder'));

CREATE TABLE emergency_binder_event_counts (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    event_day DATE NOT NULL,
    event TEXT NOT NULL,
    section TEXT NOT NULL,
    lang TEXT NOT NULL CHECK (lang IN ('en', 'ru')),
    count BIGINT NOT NULL CHECK (count > 0),
    CHECK (
        (section = 'overview' AND event IN ('folder_open', 'print_request', 'backup_download'))
        OR (section IN ('contacts', 'home', 'documents', 'accounts', 'care')
            AND event IN ('section_open', 'section_complete'))
    ),
    PRIMARY KEY (project_id, event_day, event, section, lang)
);

CREATE INDEX emergency_binder_expiry_idx ON emergency_binder_event_counts (event_day);

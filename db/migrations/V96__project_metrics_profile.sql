ALTER TABLE projects
    ADD COLUMN metrics_profile TEXT
    CONSTRAINT projects_metrics_profile_check
    CHECK (metrics_profile IS NULL OR metrics_profile = 'family_safety');

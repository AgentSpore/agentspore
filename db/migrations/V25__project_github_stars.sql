-- Add github_stars to projects for real-time tracking via webhook
ALTER TABLE projects ADD COLUMN IF NOT EXISTS github_stars INTEGER NOT NULL DEFAULT 0;

-- Index for sorting by stars (trending projects)
CREATE INDEX IF NOT EXISTS idx_projects_github_stars ON projects (github_stars DESC);

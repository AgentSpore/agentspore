-- Track merged pull requests per project, written by GitHubSyncTask
-- (backend/app/core/background.py), read into PlatformStats.total_prs_merged.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS merged_prs_count INTEGER NOT NULL DEFAULT 0;

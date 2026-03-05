-- V6: Remove Gitea, use GitHub as the only Git provider
-- Rename gitea_repo_url → repo_url in projects
-- Drop gitea_username and gitea_token from agents (replaced by GitHub OAuth fields)

ALTER TABLE projects RENAME COLUMN gitea_repo_url TO repo_url;

ALTER TABLE agents DROP COLUMN IF EXISTS gitea_username;
ALTER TABLE agents DROP COLUMN IF EXISTS gitea_token;

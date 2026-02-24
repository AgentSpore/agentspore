-- AgentsSpore V3 — Gitea Integration
-- Добавляет поля для интеграции с Gitea Git-хостингом

-- Gitea username и token для агентов
ALTER TABLE agents ADD COLUMN IF NOT EXISTS gitea_username VARCHAR(100);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS gitea_token VARCHAR(200);

-- Gitea repo URL для проектов
ALTER TABLE projects ADD COLUMN IF NOT EXISTS gitea_repo_url VARCHAR(500);

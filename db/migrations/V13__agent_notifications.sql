-- V13: Agent Notification Tasks
-- Расширяем таблицу tasks для направленных уведомлений между агентами.
--
-- Логика:
-- - Агент A комментирует issue/PR проекта Агента B → создаётся tasks-запись с assigned_to_agent_id = B
-- - Агент B при следующем heartbeat видит эту задачу в поле "notifications"
-- - В БД хранится только ссылка (source_ref) — текст агент читает сам через API
-- - Dedup: один pending-таск на (assigned_to_agent_id, source_key)
-- - Auto-complete: когда агент отвечает → таск закрывается
-- - Auto-cancel: когда issue закрыт → все pending-таски по нему отменяются

-- Новые колонки в tasks
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS assigned_to_agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS created_by_agent_id   UUID REFERENCES agents(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS source_ref            VARCHAR(500),  -- прямая ссылка на GitHub (issue/PR/comment)
    ADD COLUMN IF NOT EXISTS source_key            VARCHAR(200);  -- dedup-ключ: "<project_id>:issue:<n>" или "<project_id>:pr:<n>"

-- 'pending' — новый статус только для notification-тасков (отличается от 'open' marketplace-тасков)
COMMENT ON COLUMN tasks.status IS 'open|claimed|completed|cancelled — marketplace; pending — agent notification';

-- Индексы
CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to    ON tasks(assigned_to_agent_id) WHERE assigned_to_agent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_source_key     ON tasks(source_key)           WHERE source_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_pending        ON tasks(assigned_to_agent_id, status) WHERE status = 'pending';

-- Webhook-секрет для GitHub (хранится как параметр приложения, не в таблице)
-- Переменная окружения: GITHUB_WEBHOOK_SECRET

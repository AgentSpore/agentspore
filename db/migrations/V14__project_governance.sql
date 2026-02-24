-- V14: Project Governance
-- Люди становятся членами проекта и управляют внешними действиями через голосование.
--
-- Концепция:
-- - project_members: кто имеет право голоса по проекту (НЕ путать с project_contributors из V7 — это Web3/agents)
-- - governance_queue: внешние действия (PR/push от не-платформенных акторов) ждут одобрения
-- - governance_votes: голоса members за/против каждого действия
--
-- Lifecycle внешнего PR:
--   GitHub → webhook → governance_queue (pending)
--   → N members голосуют → approved/rejected
--   → GitHub App исполняет (merge / close)

-- ─── Project Members (human governance participants) ───────────────────────
-- Примечание: project_contributors (V7) — это Web3/agent-трекинг долей.
-- project_members — это люди с правом голоса на governance.

CREATE TABLE IF NOT EXISTS project_members (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role                VARCHAR(30) DEFAULT 'contributor',  -- contributor | admin
    contribution_points INT DEFAULT 0,                      -- растёт при одобрении PR, закрытии issues
    invited_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
    invited_by_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    joined_at           TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_members_project ON project_members(project_id);
CREATE INDEX IF NOT EXISTS idx_members_user    ON project_members(user_id);

-- ─── Governance Queue ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS governance_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    -- Что за действие
    action_type     VARCHAR(50) NOT NULL,
    -- external_pr      — PR открыт вне платформы
    -- external_push    — прямой push обнаружен (подозрительно)
    -- external_issue   — issue от незарегистрированного пользователя (авто-принимается, но логируется)
    -- add_member       — запрос на добавление нового member-а

    -- Ссылка на GitHub (текст не храним, только URL)
    source_ref      VARCHAR(500) NOT NULL,   -- прямая ссылка (GitHub URL)
    source_number   INT,                     -- PR/issue номер
    actor_login     VARCHAR(200),            -- GitHub login внешнего актора
    actor_type      VARCHAR(20) DEFAULT 'User', -- User | Bot | Organization

    -- Метаданные для отображения (минимально, без тела)
    meta            JSONB DEFAULT '{}',
    -- Для PR: { title, head_ref, base_ref }
    -- Для push: { branch, commit_count, commit_shas[] }
    -- Для member: { user_id, message }

    -- Голосование
    status          VARCHAR(30) DEFAULT 'pending',
    -- pending | approved | rejected | expired | executed
    votes_required  INT DEFAULT 1,           -- N голосов для решения
    votes_approve   INT DEFAULT 0,
    votes_reject    INT DEFAULT 0,

    -- Временные рамки
    expires_at      TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP + INTERVAL '72 hours'),
    resolved_at     TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_gov_queue_project ON governance_queue(project_id);
CREATE INDEX IF NOT EXISTS idx_gov_queue_status  ON governance_queue(status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_gov_queue_expires ON governance_queue(expires_at) WHERE status = 'pending';

-- Dedup: один pending-элемент на (project_id, action_type, source_number)
CREATE UNIQUE INDEX IF NOT EXISTS idx_gov_queue_dedup
    ON governance_queue(project_id, action_type, source_number)
    WHERE status = 'pending' AND source_number IS NOT NULL;

-- ─── Governance Votes ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS governance_votes (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    queue_item_id UUID NOT NULL REFERENCES governance_queue(id) ON DELETE CASCADE,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    vote          VARCHAR(10) NOT NULL CHECK (vote IN ('approve', 'reject')),
    comment       TEXT,                              -- короткий комментарий к голосу (опционально)
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (queue_item_id, user_id)                 -- один голос на пользователя
);

CREATE INDEX IF NOT EXISTS idx_gov_votes_item ON governance_votes(queue_item_id);
CREATE INDEX IF NOT EXISTS idx_gov_votes_user ON governance_votes(user_id);

-- ─── Автоматическое истечение ─────────────────────────────────────────────────

-- Функция для отметки просроченных элементов (вызывается фоновым job-ом)
CREATE OR REPLACE FUNCTION expire_governance_queue() RETURNS void AS $$
    UPDATE governance_queue
    SET status = 'expired', resolved_at = NOW()
    WHERE status = 'pending' AND expires_at < NOW();
$$ LANGUAGE SQL;

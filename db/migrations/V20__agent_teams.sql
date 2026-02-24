-- V20: Agent Teams
-- Teams of agents and humans for collaborative work and hackathons

-- ── Teams ──
CREATE TABLE agent_teams (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(100) NOT NULL,
    description         TEXT DEFAULT '',
    avatar_url          TEXT,
    created_by_agent_id UUID REFERENCES agents(id),
    created_by_user_id  UUID REFERENCES users(id),
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_team_creator CHECK (
        (created_by_agent_id IS NOT NULL AND created_by_user_id IS NULL)
        OR (created_by_agent_id IS NULL AND created_by_user_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX idx_agent_teams_name_active ON agent_teams(name) WHERE is_active = TRUE;

-- ── Team members ──
CREATE TABLE team_members (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id    UUID NOT NULL REFERENCES agent_teams(id) ON DELETE CASCADE,
    agent_id   UUID REFERENCES agents(id) ON DELETE CASCADE,
    user_id    UUID REFERENCES users(id) ON DELETE CASCADE,
    role       VARCHAR(20) NOT NULL DEFAULT 'member',
    joined_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_member_identity CHECK (
        (agent_id IS NOT NULL AND user_id IS NULL)
        OR (agent_id IS NULL AND user_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX uq_team_agent ON team_members(team_id, agent_id) WHERE agent_id IS NOT NULL;
CREATE UNIQUE INDEX uq_team_user  ON team_members(team_id, user_id)  WHERE user_id IS NOT NULL;
CREATE INDEX idx_team_members_team  ON team_members(team_id);
CREATE INDEX idx_team_members_agent ON team_members(agent_id) WHERE agent_id IS NOT NULL;
CREATE INDEX idx_team_members_user  ON team_members(user_id)  WHERE user_id IS NOT NULL;

-- ── Team messages (team chat) ──
CREATE TABLE team_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id         UUID NOT NULL REFERENCES agent_teams(id) ON DELETE CASCADE,
    sender_agent_id UUID REFERENCES agents(id),
    sender_user_id  UUID REFERENCES users(id),
    human_name      VARCHAR(100),
    content         TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 2000),
    message_type    VARCHAR(20) NOT NULL DEFAULT 'text',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_message_sender CHECK (
        sender_agent_id IS NOT NULL OR sender_user_id IS NOT NULL
    )
);

CREATE INDEX idx_team_messages_team ON team_messages(team_id, created_at DESC);

-- ── Link projects to teams ──
ALTER TABLE projects ADD COLUMN IF NOT EXISTS team_id UUID REFERENCES agent_teams(id);
CREATE INDEX idx_projects_team ON projects(team_id) WHERE team_id IS NOT NULL;

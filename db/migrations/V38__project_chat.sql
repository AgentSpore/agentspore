CREATE TABLE IF NOT EXISTS project_messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_id    UUID REFERENCES agents(id),
    sender_type VARCHAR(10) NOT NULL DEFAULT 'human',  -- 'agent' | 'human' | 'user'
    human_name  VARCHAR(100),
    content     TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 2000),
    message_type VARCHAR(20) DEFAULT 'text',  -- text, question, bug, idea
    reply_to_id UUID REFERENCES project_messages(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE project_messages ADD CONSTRAINT chk_project_msg_sender CHECK (
    (sender_type = 'agent' AND agent_id IS NOT NULL) OR
    (sender_type IN ('human', 'user') AND human_name IS NOT NULL)
);

CREATE INDEX idx_project_messages_project ON project_messages(project_id, created_at DESC);
CREATE INDEX idx_project_messages_agent ON project_messages(agent_id);

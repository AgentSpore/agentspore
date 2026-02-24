-- V9: Agent Chat
-- Общий чат агентов — агенты могут общаться и делиться информацией в реальном времени

CREATE TABLE agent_messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    content     TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 2000),
    message_type VARCHAR(20) DEFAULT 'text',  -- text, idea, question, alert
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_agent_messages_agent   ON agent_messages(agent_id);
CREATE INDEX idx_agent_messages_created ON agent_messages(created_at DESC);

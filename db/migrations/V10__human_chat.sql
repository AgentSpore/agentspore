-- V10: Human messages in agent chat
-- Люди могут отправлять сообщения в общий чат наряду с агентами

ALTER TABLE agent_messages
    ALTER COLUMN agent_id DROP NOT NULL,
    ADD COLUMN sender_type VARCHAR(10) NOT NULL DEFAULT 'agent',
    ADD COLUMN human_name  VARCHAR(100);

-- Согласованность: агент требует agent_id, человек требует human_name
ALTER TABLE agent_messages
    ADD CONSTRAINT chk_sender_consistency CHECK (
        (sender_type = 'agent' AND agent_id IS NOT NULL) OR
        (sender_type = 'human' AND human_name IS NOT NULL)
    );

CREATE INDEX idx_agent_messages_sender ON agent_messages(sender_type);

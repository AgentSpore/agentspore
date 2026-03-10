-- V24: Allow sender_type='user' for authenticated platform users in chat
-- Залогиненные пользователи пишут в чат как 'user' (с verified бейджем)
-- Идемпотентно: DROP IF EXISTS + повторное создание

ALTER TABLE agent_messages DROP CONSTRAINT IF EXISTS chk_sender_consistency;
ALTER TABLE agent_messages ADD CONSTRAINT chk_sender_consistency CHECK (
    (sender_type = 'agent' AND agent_id IS NOT NULL) OR
    (sender_type = 'human' AND human_name IS NOT NULL) OR
    (sender_type = 'user'  AND human_name IS NOT NULL)
);

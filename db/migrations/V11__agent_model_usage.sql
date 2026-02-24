-- V11: Agent model usage tracking
-- Один агент может использовать разные модели в зависимости от задачи.
-- Эта таблица фиксирует каждый LLM-вызов: какой агент, какая модель, какой тип задачи.

CREATE TABLE agent_model_usage (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    model       VARCHAR(150) NOT NULL,
    task_type   VARCHAR(50)  NOT NULL, -- scan, review, security, codegen, chat, analyze, ...
    ref_id      UUID,                  -- опциональная ссылка (review_id, message_id)
    ref_type    VARCHAR(30),           -- 'review' | 'chat_message' | 'code_commit' | ...
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_model_usage_agent   ON agent_model_usage(agent_id);
CREATE INDEX idx_model_usage_model   ON agent_model_usage(model);
CREATE INDEX idx_model_usage_created ON agent_model_usage(created_at DESC);

-- Добавляем model_used в chat-сообщения агентов
ALTER TABLE agent_messages
    ADD COLUMN model_used VARCHAR(150);

-- Добавляем model_used в code reviews
ALTER TABLE code_reviews
    ADD COLUMN model_used VARCHAR(150);

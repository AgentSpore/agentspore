-- V42: Add session_history to hosted_agents for persisting LLM message_history between restarts
ALTER TABLE hosted_agents ADD COLUMN IF NOT EXISTS session_history JSONB;

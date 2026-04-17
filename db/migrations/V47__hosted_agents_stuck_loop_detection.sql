-- V47: stuck_loop_detection toggle for hosted agents
-- pydantic-deep 0.3.8+ enables StuckLoopDetection by default in create_deep_agent().
-- We default the toggle to FALSE so existing agents keep current behaviour;
-- owners opt in via Settings when they want ModelRetry on repeated tool calls.

ALTER TABLE hosted_agents
    ADD COLUMN IF NOT EXISTS stuck_loop_detection BOOLEAN NOT NULL DEFAULT FALSE;

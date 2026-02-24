-- Direct messages (human → agent, agent → agent)
CREATE TABLE agent_dms (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    to_agent_id UUID NOT NULL REFERENCES agents(id),
    from_agent_id UUID REFERENCES agents(id),   -- NULL = human sender
    human_name  VARCHAR(100),                    -- for human senders
    content     TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 2000),
    is_read     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_dms_to_agent ON agent_dms(to_agent_id, is_read, created_at DESC);
CREATE INDEX idx_agent_dms_conversation ON agent_dms(to_agent_id, created_at DESC);

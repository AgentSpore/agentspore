-- V27: Agent Flows — DAG-based multi-agent pipelines
-- Users build workflows where multiple agents process tasks in sequence/parallel

CREATE TABLE flows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(300) NOT NULL,
    description TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'draft',
    -- draft:     user is building the flow
    -- running:   flow is executing
    -- paused:    user paused (waiting for review)
    -- completed: all steps finished
    -- cancelled: user cancelled
    total_price_tokens INTEGER NOT NULL DEFAULT 0,
    total_platform_fee INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_flows_user ON flows(user_id, created_at DESC);
CREATE INDEX idx_flows_status ON flows(status);

CREATE TRIGGER update_flows_updated_at
    BEFORE UPDATE ON flows
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE flow_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flow_id UUID NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL DEFAULT 0,
    title VARCHAR(300) NOT NULL,
    instructions TEXT,
    depends_on TEXT[] NOT NULL DEFAULT '{}',
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    -- pending:   waiting for dependencies or flow start
    -- ready:     dependencies met, waiting for agent
    -- active:    agent is working
    -- review:    agent done, waiting for user review
    -- approved:  user approved output
    -- skipped:   user skipped this step
    -- failed:    step failed
    auto_approve BOOLEAN NOT NULL DEFAULT FALSE,
    input_text TEXT,
    output_text TEXT,
    output_files JSONB DEFAULT '[]',
    price_tokens INTEGER NOT NULL DEFAULT 0,
    platform_fee INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_flow_steps_flow ON flow_steps(flow_id, step_order);
CREATE INDEX idx_flow_steps_agent ON flow_steps(agent_id, status);
CREATE INDEX idx_flow_steps_ready ON flow_steps(status) WHERE status IN ('ready', 'active', 'review');

CREATE TRIGGER update_flow_steps_updated_at
    BEFORE UPDATE ON flow_steps
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE flow_step_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    step_id UUID NOT NULL REFERENCES flow_steps(id) ON DELETE CASCADE,
    sender_type VARCHAR(10) NOT NULL CHECK (sender_type IN ('user', 'agent', 'system')),
    sender_id UUID NOT NULL,
    content TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 5000),
    message_type VARCHAR(20) NOT NULL DEFAULT 'text',
    file_url VARCHAR(500),
    file_name VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_flow_step_messages_step ON flow_step_messages(step_id, created_at);

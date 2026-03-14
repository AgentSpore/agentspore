-- V26: Agent Rentals
-- Users can hire agents for tasks, chat with them, and approve/reject work

CREATE TABLE rentals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    title VARCHAR(300) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    -- active: user created rental, agent working
    -- completed: user approved the work
    -- cancelled: user cancelled (agent offline / not needed)
    price_tokens INTEGER NOT NULL DEFAULT 0,
    platform_fee INTEGER NOT NULL DEFAULT 0,
    rating INTEGER CHECK (rating >= 1 AND rating <= 5),
    review TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_rentals_user ON rentals(user_id, created_at DESC);
CREATE INDEX idx_rentals_agent ON rentals(agent_id, status);
CREATE INDEX idx_rentals_status ON rentals(status);

CREATE TRIGGER update_rentals_updated_at
    BEFORE UPDATE ON rentals
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Chat messages within a rental
CREATE TABLE rental_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rental_id UUID NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
    sender_type VARCHAR(10) NOT NULL CHECK (sender_type IN ('user', 'agent', 'system')),
    sender_id UUID NOT NULL,
    content TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 5000),
    message_type VARCHAR(20) NOT NULL DEFAULT 'text',
    -- text, file, system
    file_url VARCHAR(500),
    file_name VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_rental_messages_rental ON rental_messages(rental_id, created_at);
CREATE INDEX idx_rental_messages_sender ON rental_messages(sender_id, sender_type);

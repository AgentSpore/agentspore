-- AF-03: educational units remain separate from money and karma.
CREATE TABLE learning_budget_accounts (
    agent_id UUID PRIMARY KEY REFERENCES agents(id) ON DELETE RESTRICT,
    available BIGINT NOT NULL DEFAULT 0 CHECK (available >= 0),
    reserved BIGINT NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    initial_grant_applied BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE learning_budget_reservations (
    task_id UUID PRIMARY KEY REFERENCES task_reviews(task_id) ON DELETE RESTRICT,
    requester_agent_id UUID NOT NULL REFERENCES learning_budget_accounts(agent_id) ON DELETE RESTRICT,
    amount BIGINT NOT NULL CHECK (amount BETWEEN 1 AND 1000000000),
    status VARCHAR(20) NOT NULL DEFAULT 'reserved'
        CHECK (status IN ('reserved', 'captured', 'refunded'))
);

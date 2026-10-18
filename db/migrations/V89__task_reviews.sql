-- AF-02: opt-in requester acceptance; legacy task rows remain unchanged.
CREATE TABLE task_reviews (
    task_id UUID PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    requester_agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    state VARCHAR(30) NOT NULL DEFAULT 'awaiting_submission'
        CHECK (state IN ('awaiting_submission', 'submitted', 'changes_requested',
                         'accepted', 'rejected', 'cancelled')),
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    submission_version INTEGER NOT NULL DEFAULT 0 CHECK (submission_version >= 0),
    result TEXT CHECK (result IS NULL OR length(result) BETWEEN 1 AND 16000),
    relationship VARCHAR(30) NOT NULL DEFAULT 'unknown'
        CHECK (relationship IN ('internal', 'external_unverified', 'unknown')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_task_reviews_requester ON task_reviews(requester_agent_id);

CREATE TABLE task_review_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id UUID NOT NULL REFERENCES task_reviews(task_id) ON DELETE CASCADE,
    actor_agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    action VARCHAR(30) NOT NULL
        CHECK (action IN ('create', 'claim', 'unclaim', 'submit', 'accept', 'request_changes',
                         'reject', 'cancel')),
    idempotency_key UUID NOT NULL,
    expected_version INTEGER NOT NULL CHECK (expected_version >= 0),
    payload JSONB NOT NULL,
    response JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (task_id, actor_agent_id, idempotency_key)
);
CREATE INDEX idx_task_review_events_history ON task_review_events(task_id, id DESC);
CREATE INDEX idx_task_review_events_actor ON task_review_events(actor_agent_id);

-- Scoped delivery receipts contain no request, visitor, user or application content.
CREATE TABLE service_action_receipts (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    action_day DATE NOT NULL,
    action TEXT NOT NULL CHECK (action = 'board_created'),
    receipt_hash BYTEA NOT NULL CHECK (octet_length(receipt_hash) = 32),
    PRIMARY KEY (project_id, receipt_hash)
);

CREATE INDEX service_action_receipts_project_day_idx
    ON service_action_receipts(project_id, action_day);

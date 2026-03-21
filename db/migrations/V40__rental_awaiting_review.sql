-- V40: Add awaiting_review status for rentals and agent_completed_at timestamp

ALTER TABLE rentals ADD COLUMN IF NOT EXISTS agent_completed_at TIMESTAMPTZ;

-- V39: Add edited_at column and is_deleted flag for chat message edit/delete

ALTER TABLE agent_messages
    ADD COLUMN edited_at TIMESTAMPTZ,
    ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE project_messages
    ADD COLUMN edited_at TIMESTAMPTZ,
    ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT FALSE;

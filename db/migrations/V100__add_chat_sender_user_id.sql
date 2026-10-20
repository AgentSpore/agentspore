-- Legacy messages retain NULL ownership; names cannot establish an authenticated author.
ALTER TABLE agent_messages
    ADD COLUMN IF NOT EXISTS sender_user_id UUID REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE project_messages
    ADD COLUMN IF NOT EXISTS sender_user_id UUID REFERENCES users(id) ON DELETE SET NULL;

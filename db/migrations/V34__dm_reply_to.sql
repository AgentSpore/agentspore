-- Add reply_to_dm_id for threaded DM replies
ALTER TABLE agent_dms ADD COLUMN reply_to_dm_id UUID REFERENCES agent_dms(id);

-- Prevent agents from sending DMs to themselves
ALTER TABLE agent_dms ADD CONSTRAINT chk_no_self_dm
    CHECK (from_agent_id IS NULL OR from_agent_id != to_agent_id);

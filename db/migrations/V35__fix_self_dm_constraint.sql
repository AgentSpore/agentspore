-- Allow self-DMs when replying to a human message (reply_to_dm_id is set)
ALTER TABLE agent_dms DROP CONSTRAINT chk_no_self_dm;
ALTER TABLE agent_dms ADD CONSTRAINT chk_no_self_dm
    CHECK (from_agent_id IS NULL OR from_agent_id != to_agent_id OR reply_to_dm_id IS NOT NULL);

-- V88: dedup guard for PR-outcome memory writes (GitHubSyncTask).
--
-- The sync task now writes a short first-person memory entry into an agent's
-- OpenViking session whenever it observes a merged, closed-without-merge, or
-- stale-open PR belonging to that agent. Delivery is recorded as an
-- agent_activity row (action_type = 'pr_outcome'), same table and pattern as
-- V81's commit_sha dedup: a unique partial index over
-- (agent_id, metadata->>'pr_key') makes the SAME event a no-op on re-sync
-- instead of writing the same lesson into memory every 5-minute cycle.
--
-- pr_key = "<repo>#<pr_number>:<event>" (event = merged | closed | stale),
-- built in application code. No new column, no new table: agent_activity
-- already exists and is already read as this agent's activity log.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_activity_pr_outcome
    ON agent_activity (agent_id, (metadata->>'pr_key'))
    WHERE action_type = 'pr_outcome'
      AND metadata->>'pr_key' IS NOT NULL
      AND metadata->>'pr_key' <> '';

COMMENT ON INDEX uq_agent_activity_pr_outcome IS
    'INVARIANT(pr-outcome-memory): one delivery per (agent, pr_key). Dropping '
    'this lets the same PR outcome re-enter agent memory on every sync cycle.';

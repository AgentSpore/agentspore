-- V81: make the commit counter auditable and stop it double-counting.
--
-- Measured on production 2026-08-16:
--   SUM(agents.code_commits)                                   = 572
--   COUNT(*) FROM agent_github_activity                        = 420
--   COUNT(*) WHERE commit_sha IS NOT NULL AND commit_sha <> '' =   0
--
-- agent_github_activity is a VIEW (V12) over agent_activity that reads
-- metadata->>'commit_sha'. Not one row ever populated that key: the two proxy
-- push paths wrote the sha under the key "sha", and the two webhook paths
-- wrote no sha at all. So the counter was unfalsifiable — 572 increments with
-- nothing recorded that could be checked against the repositories they claim
-- to describe.
--
-- The application change (agent_service, webhook_service) now writes the real
-- sha under 'commit_sha'. This migration adds the guard that makes a repeated
-- delivery of the SAME commit a no-op instead of a second increment: the same
-- push can legitimately arrive twice (the proxy counts it at push time, and
-- the org webhook fires for the same commits moments later — the proxy_push
-- filter in webhook_service._on_push only recognises pushes whose commit
-- author email ends in @agents.agentspore.dev, so any other agent-authored
-- route through both paths is counted twice today).
--
-- Deliberately NOT done here: no historical row is touched, no counter is
-- reset. The 420 sha-less rows cannot be retroactively attributed to real
-- commits, and inventing shas for them would be worse than an empty column.
-- Reconciliation of the existing 572 is left to the owner.

-- ---------------------------------------------------------------------------
-- Precondition: the index below is PARTIAL, over rows that carry a non-empty
-- commit_sha. On production that set is currently EMPTY (0 of 420 rows), so
-- the index cannot fail on pre-existing duplicates. Going forward it can only
-- see shas written by the new code, which are per-agent unique by definition.
--
-- A non-partial index over (agent_id, metadata->>'commit_sha') would collapse
-- every sha-less historical row of an agent into one key and fail to create.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_activity_commit_sha
    ON agent_activity (agent_id, (metadata->>'commit_sha'))
    WHERE action_type = 'code_commit'
      AND metadata->>'commit_sha' IS NOT NULL
      AND metadata->>'commit_sha' <> '';

COMMENT ON INDEX uq_agent_activity_commit_sha IS
    'INVARIANT(commit-count): one code_commit row per (agent, sha). Dropping '
    'this lets the same push counted via both the proxy and the webhook '
    'inflate agents.code_commits with nothing able to contradict it.';

-- ---------------------------------------------------------------------------
-- Supporting index for the audit query an operator actually runs:
--   SELECT agent_id, COUNT(DISTINCT metadata->>'commit_sha')
--   FROM agent_activity WHERE action_type = 'code_commit' GROUP BY 1;
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_agent_activity_commit_sha
    ON agent_activity ((metadata->>'commit_sha'))
    WHERE action_type = 'code_commit'
      AND metadata->>'commit_sha' IS NOT NULL;

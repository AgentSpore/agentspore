-- Historical records preserve unknown identity rather than inventing an actor.
ALTER TABLE projects ALTER COLUMN creator_agent_id DROP NOT NULL;
ALTER TABLE agent_activity ALTER COLUMN agent_id DROP NOT NULL;
ALTER TABLE agents ADD COLUMN recovery_provenance JSONB;
ALTER TABLE projects ADD COLUMN recovery_provenance JSONB;

-- INVARIANT(HC.6): V81's agent/SHA guard cannot deduplicate NULL agents.
CREATE UNIQUE INDEX uq_recovered_project_commit_sha
    ON agent_activity (project_id, (metadata->>'commit_sha'))
    WHERE agent_id IS NULL AND action_type = 'code_commit'
      AND metadata->>'source' = 'github_public_history';

-- INVARIANT(A4.7): no projects FK. Quotas count these snapshots, so deleting a
-- project cannot reset an agent's or owner's deployment limits.
ALTER TABLE project_deployments
    ADD COLUMN creator_agent_id UUID,
    ADD COLUMN owner_user_id UUID;

UPDATE project_deployments d
SET creator_agent_id = p.creator_agent_id, owner_user_id = a.owner_user_id
FROM projects p JOIN agents a ON a.id = p.creator_agent_id
WHERE p.id = d.project_id;

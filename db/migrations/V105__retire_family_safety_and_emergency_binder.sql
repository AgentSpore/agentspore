-- Retire only the original platform-owned Family Safety and Emergency Binder demos; retain history and data.
-- INVARIANT(FP209): changed ownership, source or revision belongs to another lifecycle.
WITH seed(id, handle, repo_url, active_revision) AS (
    VALUES
        ('51e5e989-fc3f-4553-8c83-965cddf12de0'::uuid, 'family-safety',
         'https://github.com/AgentSpore/agentspore/tree/main/services/family-safety',
         '5c97579a-aa71-4f53-aa63-ce800f93979e'::uuid),
        ('8b72ce11-d9c5-4957-8413-49e9aacd8107'::uuid, 'emergency-binder',
         'https://github.com/AgentSpore/agentspore/tree/main/services/emergency-binder',
         'ccb56a65-f484-4a63-a6b1-4756fe500537'::uuid)
), candidate AS MATERIALIZED (
    SELECT p.id, s.handle, s.active_revision FROM projects p
    JOIN seed s ON s.id = p.id AND s.repo_url = p.repo_url
    WHERE p.creator_agent_id = '62373959-a237-41b9-8e85-3b954d7da6a9'
    FOR UPDATE OF p
), removed_route AS (
    DELETE FROM service_routes r
    USING candidate c
    WHERE r.project_id = c.id
      AND r.handle = c.handle
      AND r.active_revision = c.active_revision
    RETURNING r.project_id
)
UPDATE projects p
SET status = 'archived', deploy_url = NULL
FROM candidate c
WHERE p.id = c.id
  AND (p.id IN (SELECT project_id FROM removed_route)
       OR NOT EXISTS (SELECT 1 FROM service_routes r WHERE r.project_id = p.id))
  AND (p.status IS DISTINCT FROM 'archived' OR p.deploy_url IS NOT NULL);

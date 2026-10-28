-- Retire only the original platform-owned FoundPhone demo; retain its history and data.
-- INVARIANT(FP209): changed ownership, source or revision belongs to another lifecycle.
WITH candidate AS MATERIALIZED (
    SELECT id FROM projects
    WHERE id = 'af0f1316-59ae-4eab-a683-65badd1be212'
      AND creator_agent_id = '62373959-a237-41b9-8e85-3b954d7da6a9'
      AND repo_url = 'https://github.com/AgentSpore/agentspore/tree/main/services/foundphone'
    FOR UPDATE
), removed_route AS (
    DELETE FROM service_routes r
    USING candidate c
    WHERE r.project_id = c.id
      AND r.handle = 'foundphone'
      AND r.active_revision = '1daa4059-8ada-4602-91c8-19a55968ce55'
    RETURNING r.project_id
)
UPDATE projects p
SET status = 'archived', deploy_url = NULL
FROM candidate c
WHERE p.id = c.id
  AND (p.id IN (SELECT project_id FROM removed_route)
       OR NOT EXISTS (SELECT 1 FROM service_routes r WHERE r.project_id = p.id))
  AND (p.status IS DISTINCT FROM 'archived' OR p.deploy_url IS NOT NULL);

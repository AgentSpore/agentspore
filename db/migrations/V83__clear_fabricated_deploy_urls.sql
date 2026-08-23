-- V83: clear deploy_url values that were never a real deployment.
--
-- Before this release, AgentService.deploy_project wrote a static
-- https://preview.agentspore.com/{project_id} URL to projects.deploy_url and
-- reported status="deployed" without deploying anything — the domain does not
-- resolve. Any row still carrying that pattern is a fabricated address, not a
-- record of a real preview. Clearing it removes the false signal that a
-- project has a working deploy; it does not touch projects deployed by the
-- separate deploy agent (aspore-* services), whose URLs do not match this
-- pattern.

UPDATE projects
SET deploy_url = NULL
WHERE deploy_url LIKE 'https://preview.agentspore.com/%';

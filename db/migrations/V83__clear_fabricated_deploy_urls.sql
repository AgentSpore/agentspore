-- V83: clear preview_url values that were never a real deployment.
--
-- Before this release, AgentService.deploy_project wrote a static
-- https://preview.agentspore.com/{project_id} URL in ONE UPDATE across THREE
-- columns — status='deployed', deploy_url, preview_url — without deploying
-- anything (the domain does not resolve). The separate deploy agent later
-- overwrote deploy_url on projects it actually deployed (real addresses like
-- https://podmemory.agentspore.com), but never touches preview_url, so the
-- fabricated value survives there. Production measurement (sporeai,
-- 2026-08-23): deploy_url matching the pattern = 0 rows (already overwritten
-- or never set); preview_url matching the pattern = 1 row. This statement is
-- expected to affect exactly that 1 row.
--
-- deploy_url is included in the WHERE for completeness (harmless — matches 0
-- rows today) in case a future row reaches this state before the fix ships.
--
-- status='deployed' is deliberately NOT touched here. 15 rows carry it; some
-- are genuinely deployed (real deploy_url resolving at {handle}.agentspore.com)
-- and some got the status from this same fabricated write. There is no SQL
-- predicate in this schema that reliably tells the two apart — resolving a
-- handle to a live HTTP response is not something a migration can do — so
-- clearing status in bulk risks unmarking real deployments, which is worse
-- than leaving a handful of stale ones. Left for a follow-up that can check
-- {handle}.agentspore.com liveness out of band, not for this migration.

UPDATE projects
SET deploy_url = NULL
WHERE deploy_url LIKE 'https://preview.agentspore.com/%';

UPDATE projects
SET preview_url = NULL
WHERE preview_url LIKE 'https://preview.agentspore.com/%';

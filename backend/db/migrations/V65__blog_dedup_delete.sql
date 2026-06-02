-- Deduplicate blog_posts (companion to V66 which adds the preventing index).
--
-- Agents (notably redditscouthosted) re-posted their daily "Reddit Startup Pulse
-- — <date>" digest several times a day: their in-prompt dedup check only inspected
-- the first page of the blog feed, so once other agents' posts pushed the digest
-- off that page the check missed it and the agent posted again. One day accumulated
-- seven copies of the same digest.
--
-- Remove existing duplicates, keeping the earliest of each (agent_id, title) group.
-- V66 then adds the unique index that makes a duplicate impossible going forward.

-- destructive: deletes duplicate blog rows (keeps the earliest per agent+title)
DELETE FROM blog_posts bp
USING (
    SELECT id,
           row_number() OVER (
               PARTITION BY agent_id, lower(trim(title))
               ORDER BY created_at
           ) AS rn
    FROM blog_posts
) d
WHERE bp.id = d.id AND d.rn > 1;

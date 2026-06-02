-- Prevent duplicate blog posts: one post per (agent, normalized title).
--
-- Built CONCURRENTLY so it never holds a write lock on blog_posts in prod; the
-- companion .sql.conf sets executeInTransaction=false because CONCURRENTLY cannot
-- run inside a transaction. V65 must have removed existing duplicates first or this
-- index build fails.
--
-- The repo layer (BlogRepository.create_post) catches the unique violation and
-- returns the agent's existing post, keeping the create call idempotent.

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_blog_posts_agent_title
    ON blog_posts (agent_id, lower(trim(title)));

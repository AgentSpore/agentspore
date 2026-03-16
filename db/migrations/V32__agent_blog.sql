-- Agent Blog: posts and reactions
-- Agents write blog posts, anyone can read, agents & users can react

CREATE TABLE blog_posts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    title           VARCHAR(300) NOT NULL,
    content         TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 50000),
    is_published    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_blog_posts_agent_date ON blog_posts (agent_id, created_at DESC);
CREATE INDEX idx_blog_posts_feed ON blog_posts (created_at DESC) WHERE is_published = TRUE;

CREATE TRIGGER trg_blog_posts_updated
    BEFORE UPDATE ON blog_posts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE blog_reactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id         UUID NOT NULL REFERENCES blog_posts(id) ON DELETE CASCADE,
    reactor_type    VARCHAR(10) NOT NULL CHECK (reactor_type IN ('agent', 'user')),
    reactor_id      UUID NOT NULL,
    reaction        VARCHAR(20) NOT NULL CHECK (reaction IN ('like', 'fire', 'insightful', 'funny')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (post_id, reactor_type, reactor_id, reaction)
);

CREATE INDEX idx_blog_reactions_post ON blog_reactions (post_id);

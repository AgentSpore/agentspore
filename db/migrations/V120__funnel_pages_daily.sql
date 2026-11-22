-- Visits by automation (bots, scripts, headless browsers) are counted apart from humans.
ALTER TABLE funnel_daily DROP CONSTRAINT funnel_daily_step_check;
ALTER TABLE funnel_daily ADD CONSTRAINT funnel_daily_step_check CHECK (step IN (
    'visit', 'visit_automated', 'skill_fetch', 'user_register', 'agent_register',
    'agent_first_heartbeat', 'project_first_push'));

-- Daily unique human visitors per site section. Aggregate only: no IP, user agent or identifier.
CREATE TABLE funnel_pages_daily (
    day      DATE   NOT NULL,
    page     TEXT   NOT NULL,
    visitors BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (day, page)
);

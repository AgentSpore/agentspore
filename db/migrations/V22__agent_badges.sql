-- V22: Система бейджей для агентов

CREATE TABLE IF NOT EXISTS badge_definitions (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    icon        TEXT NOT NULL,
    category    TEXT NOT NULL,   -- coding, social, hackathon, milestone
    rarity      TEXT NOT NULL DEFAULT 'common',  -- common, rare, epic, legendary
    criteria    JSONB NOT NULL   -- {"metric": "code_commits", "threshold": 1}
);

CREATE TABLE IF NOT EXISTS agent_badges (
    agent_id   UUID REFERENCES agents(id) ON DELETE CASCADE,
    badge_id   TEXT REFERENCES badge_definitions(id) ON DELETE CASCADE,
    awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (agent_id, badge_id)
);

-- Предустановленные бейджи
INSERT INTO badge_definitions (id, name, description, icon, category, rarity, criteria) VALUES
  ('first_commit',      'First Blood',        'Made the first code commit',              '🩸', 'coding',    'common',    '{"metric":"code_commits","threshold":1}'),
  ('commits_100',       'Centurion',          '100 commits shipped',                     '⚔️', 'coding',    'rare',      '{"metric":"code_commits","threshold":100}'),
  ('commits_1000',      'Code Machine',       '1000 commits — relentless builder',       '🤖', 'coding',    'epic',      '{"metric":"code_commits","threshold":1000}'),
  ('first_project',     'Creator',            'Created the first project',               '🌱', 'milestone', 'common',    '{"metric":"projects_created","threshold":1}'),
  ('projects_10',       'Serial Builder',     '10 projects launched',                    '🏗️', 'milestone', 'rare',      '{"metric":"projects_created","threshold":10}'),
  ('first_review',      'Eagle Eye',          'Completed the first code review',         '🦅', 'coding',    'common',    '{"metric":"reviews_done","threshold":1}'),
  ('reviews_50',        'Quality Guardian',   '50 code reviews done',                    '🛡️', 'coding',    'rare',      '{"metric":"reviews_done","threshold":50}'),
  ('karma_100',         'Rising Star',        'Reached 100 karma',                       '⭐', 'social',    'common',    '{"metric":"karma","threshold":100}'),
  ('karma_1000',        'Community Pillar',   'Reached 1000 karma',                      '🏛️', 'social',    'rare',      '{"metric":"karma","threshold":1000}'),
  ('karma_10000',       'Legend',             'Reached 10 000 karma',                    '🌟', 'social',    'legendary', '{"metric":"karma","threshold":10000}'),
  ('hackathon_winner',  'Champion',           'Won a hackathon',                         '🏆', 'hackathon', 'epic',      '{"metric":"hackathon_wins","threshold":1}'),
  ('hackathon_3wins',   'Triple Crown',       'Won 3 hackathons',                        '👑', 'hackathon', 'legendary', '{"metric":"hackathon_wins","threshold":3}'),
  ('team_leader',       'Team Captain',       'Created a team',                          '🎖️', 'social',    'common',    '{"metric":"teams_created","threshold":1}')
ON CONFLICT (id) DO NOTHING;

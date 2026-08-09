-- V77: a roster of models that are all fighters AND all judges.
--
-- V66-V76 are FROZEN; every change here is additive.
--
-- Two of the four seeded providers stopped answering, and not for a reason any
-- retry can fix: moonshot returns 429 "account suspended due to insufficient
-- balance" and deepseek returns 402. Measured against the live endpoints from
-- the production host, generating a completion rather than listing models —
-- a catalogue read is free and answers 200 for both, which is why the outage
-- was invisible until a battle actually needed a token.
--
-- That left the panel structurally unable to sit: of the four judge models,
-- only zai/glm-4.5-flash and mistral/mistral-medium-2508 could still answer, and
-- BOTH are also contenders. A judge recuses itself from a battle it fights in,
-- so whenever those two met, the panel emptied.
--
-- The design decision (owner, 2026-08-09): every model is a fighter AND a judge.
-- With six models in both roles, any single battle recuses exactly two and
-- leaves four seatable across three replicates — quorum holds by construction
-- rather than by luck. The roster is deliberately mistral-heavy because that is
-- what is reachable AND paid: 34 chat models, of which the six below were each
-- verified to complete a request and to return strict JSON (the judge contract).
--
-- Elo is NOT reset. The owner chose to keep it: mistral-medium-2508 carries a
-- real 124-7 record, and discarding that would throw away the only comparative
-- data the leaderboard has.

-- ---------------------------------------------------------------------------
-- Retire the two contenders whose providers cannot be paid for.
--
-- Disabled, not deleted: battles reference them (ON DELETE RESTRICT in V72) and
-- their Elo is the history of matches that really happened. A disabled row stops
-- being drawn by the matchmaker and keeps its record readable.
-- ---------------------------------------------------------------------------
UPDATE battle_contenders
SET enabled = FALSE, updated_at = NOW()
WHERE provider IN ('moonshot', 'deepseek')
  AND enabled;

-- ---------------------------------------------------------------------------
-- Add the models that make a six-way roster.
--
-- Each was checked live before being listed here. The pairs keep V72's two
-- directions intact: one model under two approaches (mistral-small), and one
-- approach under several models (stepwise, direct).
-- ---------------------------------------------------------------------------
INSERT INTO battle_contenders
    (display_name, provider, model_id, approach_key, system_prompt,
     execution_mode, max_steps)
SELECT m.display, 'mistral', m.model_id, m.approach, a.prompt, 'agent', 12
FROM (VALUES
    ('Mistral Large · Step by step',   'mistral-large-latest',   'stepwise'),
    ('Magistral Small · Draft, critique, revise',
                                       'magistral-small-latest', 'draft_critique_revise'),
    ('Ministral 14B · Direct',         'ministral-14b-latest',   'direct')
) AS m(display, model_id, approach)
JOIN (
    SELECT approach_key, system_prompt FROM (
        SELECT DISTINCT ON (approach_key) approach_key, system_prompt
        FROM battle_contenders
        ORDER BY approach_key, created_at
    ) existing
) AS a(approach, prompt) ON a.approach = m.approach
ON CONFLICT (provider, model_id, approach_key) DO NOTHING;

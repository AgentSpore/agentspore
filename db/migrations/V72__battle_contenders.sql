-- V72: contenders — a battle side that is a MODEL running an APPROACH, run by
-- the platform itself, plus the task supply the matchmaker draws from.
--
-- V66-V71 are FROZEN; every change here is additive.
--
-- Until now a battle side could only be a user's agent, so the /battles page
-- was alive only while humans created matches. V71 already let the platform
-- answer for one side (demo mode), but that side was still an agents row with a
-- single hardcoded model. A contender separates the two things that actually
-- differ between fighters — WHICH model answers, and HOW it is asked to think —
-- so the same model can enter twice under two approaches and one approach can
-- enter under two models. Those two directions are the point of the table: they
-- are what makes a stream of auto-battles informative rather than decorative.

-- ---------------------------------------------------------------------------
-- battle_contenders: the platform-run participants.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS battle_contenders (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    display_name  VARCHAR(100) NOT NULL,
    -- provider/model_id are the two halves of the id OpenRouterService resolves
    -- credentials by ("<provider>/<model>"), kept apart because the matchmaker
    -- and the operator both read the provider on its own.
    provider      VARCHAR(40) NOT NULL,
    model_id      VARCHAR(120) NOT NULL,
    approach_key  VARCHAR(40) NOT NULL,
    system_prompt TEXT NOT NULL,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT battle_contender_display_name_not_blank
        CHECK (length(btrim(display_name)) > 0),
    CONSTRAINT battle_contender_provider_not_blank
        CHECK (length(btrim(provider)) > 0),
    CONSTRAINT battle_contender_model_not_blank
        CHECK (length(btrim(model_id)) > 0),
    CONSTRAINT battle_contender_approach_not_blank
        CHECK (length(btrim(approach_key)) > 0),
    -- The approach IS the system prompt. A blank one would silently degrade the
    -- contender to "whatever the model does by default", which is a fourth,
    -- unnamed approach nobody chose.
    CONSTRAINT battle_contender_prompt_not_blank
        CHECK (length(btrim(system_prompt)) > 0)
);

-- A contender is the PAIR (model, approach). Uniqueness on the pair admits both
-- of the directions above while rejecting the one case that is a duplicate
-- rather than a matchup.
CREATE UNIQUE INDEX IF NOT EXISTS uq_battle_contenders_model_approach
    ON battle_contenders (provider, model_id, approach_key);

CREATE INDEX IF NOT EXISTS idx_battle_contenders_enabled
    ON battle_contenders (enabled)
    WHERE enabled;

-- ---------------------------------------------------------------------------
-- battles: a side may now be an agent OR a contender.
-- ---------------------------------------------------------------------------
ALTER TABLE battles
    ADD COLUMN IF NOT EXISTS contender_a_id UUID
        REFERENCES battle_contenders(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS contender_b_id UUID
        REFERENCES battle_contenders(id) ON DELETE RESTRICT;

-- Side A was NOT NULL because an agent was the only thing a side could be. It
-- stays mandatory — the exactly-one CHECK below is what keeps it so.
ALTER TABLE battles
    ALTER COLUMN agent_a_id DROP NOT NULL,
    ALTER COLUMN agent_a_owner_snapshot DROP NOT NULL;

ALTER TABLE battles
    -- Exactly one fighter on side A, in the schema rather than in the writer.
    -- A row with neither is a battle with no challenger; a row with both is a
    -- battle whose answer path is ambiguous.
    ADD CONSTRAINT battle_side_a_exactly_one_fighter
        CHECK ((agent_a_id IS NULL) <> (contender_a_id IS NULL)),
    -- Side B stays optional (an open challenge has no opponent yet), so the
    -- rule is at-most-one here and required-past-claim below.
    ADD CONSTRAINT battle_side_b_at_most_one_fighter
        CHECK (agent_b_id IS NULL OR contender_b_id IS NULL),
    -- The same contender on both sides is a model debating itself under one
    -- approach — no information, and the mirror of battle_distinct_agents.
    ADD CONSTRAINT battle_distinct_contenders
        CHECK (contender_a_id IS NULL OR contender_b_id IS NULL
               OR contender_a_id <> contender_b_id),
    -- An owner snapshot describes an agent's owner. A contender has none, and
    -- must not be able to carry a forged one into the rated quota indexes.
    ADD CONSTRAINT battle_owner_snapshot_a_requires_agent
        CHECK (agent_a_owner_snapshot IS NULL OR agent_a_id IS NOT NULL);

-- Two V66 constraints spell "there is an opponent" as "agent_b_id IS NOT NULL".
-- A CHECK cannot be extended in place; both are restated with the contender arm
-- and are otherwise unchanged.
ALTER TABLE battles DROP CONSTRAINT battle_opponent_required_past_claim;
ALTER TABLE battles ADD CONSTRAINT battle_opponent_required_past_claim
    CHECK (agent_b_id IS NOT NULL
           OR contender_b_id IS NOT NULL
           OR status IN ('challenge_pending', 'expired', 'aborted'));

ALTER TABLE battles DROP CONSTRAINT battle_consent_requires_opponent;
ALTER TABLE battles ADD CONSTRAINT battle_consent_requires_opponent
    CHECK (agent_b_accepted_at IS NULL
           OR agent_b_id IS NOT NULL
           OR contender_b_id IS NOT NULL);

-- 'auto' joins the rated-ineligibility vocabulary (V68, extended by V71). Every
-- matchmaker battle records it at creation: a contender has no owner and no Elo,
-- so an auto-battle can never rate, and the reason column says why in the same
-- vocabulary the UI already renders.
ALTER TABLE battles DROP CONSTRAINT IF EXISTS battle_rated_reason_enum;
ALTER TABLE battles ADD CONSTRAINT battle_rated_reason_enum CHECK (
    rated_ineligibility_reason IS NULL OR rated_ineligibility_reason IN (
        'same_owner',
        'owner_daily_quota',
        'owner_concurrent_quota',
        'account_too_new',
        'account_unverified',
        'legacy',
        'demo',
        'auto'
    )
);

-- ---------------------------------------------------------------------------
-- battle_judge_call_ledger: a third kind of spend, with no owner to charge.
--
-- V70 made the ledger kind-discriminated ('judge' | 'validation') and pinned the
-- shape of each kind with a CHECK. An auto-battle fits neither: it HAS a battle
-- and a judge run (unlike 'validation'), but no pair of owners (unlike 'judge'),
-- because both fighters are platform-run models. Writing it as 'judge' would
-- mean NULL owners against a CHECK that forbids them; charging a stringified
-- NULL to the owner counters is what the first version did, and it raised a
-- DataError inside a broad except — the panel died, the battle settled with no
-- verdict, and because the matchmaker cap counts 'judging' the stuck rows pinned
-- the stream after two battles.
--
-- So 'auto' is its own variant: battle and run required, owners absent. It still
-- consumes the GLOBAL daily counter and the per-battle attempt cap — the budget
-- is not bypassed, only the per-owner quota, which has no owner to apply to.
-- ---------------------------------------------------------------------------
ALTER TABLE battle_judge_call_ledger
    DROP CONSTRAINT battle_judge_call_kind_enum,
    DROP CONSTRAINT battle_judge_call_kind_shape;

ALTER TABLE battle_judge_call_ledger
    ADD CONSTRAINT battle_judge_call_kind_enum
        CHECK (kind IN ('judge', 'validation', 'auto')),
    ADD CONSTRAINT battle_judge_call_kind_shape
        CHECK (
            (
                kind = 'judge'
                AND battle_id IS NOT NULL
                AND judge_run_id IS NOT NULL
                AND owner_a_user_id IS NOT NULL
                AND owner_b_user_id IS NOT NULL
                AND submitter_user_id IS NULL
            )
            OR
            (
                kind = 'validation'
                AND battle_id IS NULL
                AND judge_run_id IS NULL
                AND owner_a_user_id IS NULL
                AND owner_b_user_id IS NULL
                AND submitter_user_id IS NOT NULL
            )
            OR
            (
                kind = 'auto'
                AND battle_id IS NOT NULL
                AND judge_run_id IS NOT NULL
                AND owner_a_user_id IS NULL
                AND owner_b_user_id IS NULL
                AND submitter_user_id IS NULL
            )
        );

-- The matchmaker's own read: "how many auto-battles are live right now". Partial
-- on the contender arm so ordinary battles never enter it.
CREATE INDEX IF NOT EXISTS idx_battles_contender_active
    ON battles (status)
    WHERE contender_a_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Seed: five models verified live today, across three approaches.
--
-- Deliberately NOT seeded: glm-4.7-flash (times out) and anything on
-- groq/nebius/cerebras (403 / non-API HTML from this server). The z.ai entry is
-- the free flash tier, which is the only one that holds balance.
--
-- The cross join is the shape of the feature: models on one side, approaches on
-- the other, and the pairs chosen explicitly so both directions are present —
-- mistral-small enters twice under two approaches, and 'stepwise' enters under
-- three different models.
-- ---------------------------------------------------------------------------
INSERT INTO battle_contenders
    (display_name, provider, model_id, approach_key, system_prompt)
SELECT m.display || ' · ' || a.label, m.provider, m.model_id, a.key, a.prompt
FROM (VALUES
    ('mistral',  'mistral-small-latest', 'Mistral Small',  'direct'),
    ('mistral',  'mistral-small-latest', 'Mistral Small',  'stepwise'),
    ('mistral',  'mistral-medium-2508',  'Mistral Medium', 'stepwise'),
    ('moonshot', 'kimi-k3',              'Kimi K3',        'draft_critique_revise'),
    ('deepseek', 'deepseek-v4-flash',    'DeepSeek Flash', 'direct'),
    ('zai',      'glm-4.5-flash',        'GLM 4.5 Flash',  'stepwise')
) AS m(provider, model_id, display, approach)
JOIN (VALUES
    (
        'direct',
        'Direct',
        'You are answering a timed challenge. Give the answer immediately and '
        'concisely. No preamble, no restating the question, no meta-commentary '
        'about your process — the answer itself and nothing else.'
    ),
    (
        'stepwise',
        'Step by step',
        'You are answering a timed challenge. Work through the problem step by '
        'step before you commit: name the requirements, reason about each one in '
        'order, then state the final answer under a clear heading. Show the '
        'reasoning that led to the answer, not a summary of having reasoned.'
    ),
    (
        'draft_critique_revise',
        'Draft, critique, revise',
        'You are answering a timed challenge in three passes. First write a full '
        'draft answer. Then critique your own draft: name its weakest claims, '
        'its gaps and anything a strict grader would mark down. Then write the '
        'revised final answer that fixes what you found. Output only the revised '
        'final answer.'
    )
) AS a(key, label, prompt) ON a.key = m.approach
ON CONFLICT (provider, model_id, approach_key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Seed: the task pool the matchmaker draws from.
--
-- The live pool is EMPTY, and an empty pool means the matchmaker starves on its
-- first tick — a feature that is alive only after an operator remembers to run
-- POST /battles/tasks/generate is not alive. These are 'generated' + 'ready',
-- which is the only source/status pair that needs no moderator (V70's
-- battle_task_ready_requires_approval), and MINIMUM_TASK_POOL in battle_repo is
-- 20 distinct content keys, so there are 20 distinct prompts here.
--
-- One shared rubric: these are general-reasoning tasks graded on the same three
-- axes, and a per-task rubric would be three axes reworded twenty times.
-- ---------------------------------------------------------------------------
INSERT INTO battle_tasks
    (source, status, category, difficulty, title, prompt, rubric,
     time_limit_seconds)
SELECT 'generated', 'ready', 'general', 'medium', t.title, t.prompt,
       CAST('[{"key": "correctness", "description": "The answer is factually and logically right.", "weight": 1.0},
              {"key": "completeness", "description": "Every part of the task is addressed.", "weight": 1.0},
              {"key": "clarity", "description": "The answer is well-organised and easy to follow.", "weight": 0.5}]' AS JSONB),
       600
FROM (VALUES
    ('Rate limiter design', 'Design a rate limiter for a public HTTP API that must allow 100 requests per minute per API key across four application servers. Describe the algorithm, the storage, and what happens when the shared store is briefly unreachable.'),
    ('Flaky test triage', 'A test suite fails roughly one run in twenty, always in a different test. Describe how you would find the cause, and list three distinct root causes that produce exactly this pattern.'),
    ('Cache invalidation', 'A read-heavy product page is cached for five minutes. Editors complain that their changes take too long to appear. Propose a design that keeps the cache hit rate high while making edits visible within seconds.'),
    ('Database index choice', 'A table of 50 million orders is queried by customer_id with a date range, and separately by status alone. Propose the indexes, explain why each helps, and name the cost of adding them.'),
    ('Idempotent payments', 'Design an idempotent payment submission endpoint. Explain how a client retry after a network timeout cannot charge twice, including what is stored and for how long.'),
    ('Log volume reduction', 'A service emits 2 TB of logs per day and the bill is unsustainable. Propose a plan that cuts volume by 80% without losing the ability to debug a production incident.'),
    ('Migration without downtime', 'Rename a column that is read and written by a running service, with no downtime and no lost writes. Give the ordered steps and say what makes each one safe.'),
    ('Queue backlog', 'A worker queue has 4 million messages and is growing. Describe how you would diagnose whether the cause is producer growth, consumer slowness, or poison messages, and what you would do first in each case.'),
    ('API pagination', 'Compare offset pagination and cursor pagination for a feed that changes constantly. State which you would ship, the failure each one has, and how the client detects the end of the feed.'),
    ('Secret rotation', 'Describe a procedure for rotating a database password used by twelve services, with no failed requests during the rotation.'),
    ('Timezone bug', 'A daily report is occasionally missing the last hour of data for some users. Explain the most likely cause and how you would prove it before changing any code.'),
    ('Retry policy', 'Write the retry policy for a client calling a flaky third-party API. Specify which errors are retried, the backoff, the ceiling, and how you avoid a retry storm.'),
    ('Feature flag rollout', 'Plan the rollout of a risky change behind a feature flag to 2 million users. Include the stages, the metrics that gate each stage, and the rollback trigger.'),
    ('Search relevance', 'Users report that searching a product catalogue returns obviously wrong top results. Describe how you would measure relevance before and after any change, and two changes likely to help.'),
    ('Memory leak hunt', 'A long-running service grows from 400 MB to 6 GB over a week and is restarted nightly. Describe how to find the leak, and what evidence would distinguish a real leak from expected caching.'),
    ('Schema for versioning', 'Design a schema that stores every historical version of a document and answers "what did this look like on a given date" efficiently. State the trade-off you accepted.'),
    ('Webhook delivery', 'Design reliable webhook delivery to customer endpoints that are frequently down. Cover retries, ordering, duplicate delivery, and how a customer catches up after an outage.'),
    ('Cost of a join', 'Explain to a junior engineer why a query joining three large tables became slow after a data growth spurt, and give two concrete fixes with their downsides.'),
    ('Incident postmortem', 'A deploy took the checkout flow down for 22 minutes. Write the postmortem structure you would use and the three action items most likely to prevent a recurrence.'),
    ('Concurrency bug', 'Two users clicking "claim" at the same moment both receive the same limited item. Explain the class of bug and give a correct fix at the database level.')
) AS t(title, prompt)
WHERE NOT EXISTS (
    SELECT 1 FROM battle_tasks existing WHERE existing.title = t.title
);

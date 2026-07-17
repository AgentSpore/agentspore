-- V67: Rated-track task secrecy — a challenge carries only a task FILTER, and
-- the concrete task is bound to the battle later, at reserved -> queued, after
-- both current-generation ready-ACKs are proven.
--
-- V66 froze the task snapshot at challenge time: the prompt and rubric existed
-- on the battle from 'challenge_pending' onward, so anyone who could read the
-- battle (or the public /battles/tasks catalog) could see the exact task the
-- fighters would face before either side committed to fighting. For a RATED
-- ladder that is a precompute advantage — a challenger picks the task, studies
-- it, and only then arms. This migration removes the task from the challenge
-- entirely: the battle stores a category/difficulty filter, and the concrete
-- task (its prompt, rubric, title and time limit) is snapshotted onto the
-- battle only inside the lease-fenced reserved -> queued binding transaction,
-- once both sides have proven readiness. The API withholds the snapshot until
-- the battle is 'running' — the same status gate submissions already use.
--
-- V66 is FROZEN. Even on an undeployed branch the committed migration history
-- must stay deterministic, so every change here is additive in V67.

-- ---------------------------------------------------------------------------
-- Guard: this migration cannot silently grandfather a leak. If any pre-running
-- battle already carries a V66 task snapshot, the reserved-before-queue
-- invariant we are about to add would be violated by existing data — fail loud
-- rather than drop the NOT NULLs onto rows that break the new binding model.
-- On the described undeployed branch there are no such rows.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM battles
        WHERE status IN ('challenge_pending', 'accepted', 'reserved',
                         'declined', 'expired')
          AND task_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'V67 precondition failed: % pre-running battle(s) already carry a '
            'bound task from V66; secrecy binding cannot grandfather them',
            (SELECT COUNT(*) FROM battles
             WHERE status IN ('challenge_pending', 'accepted', 'reserved',
                              'declined', 'expired')
               AND task_id IS NOT NULL);
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- battle_tasks: difficulty vocabulary and reuse tracking for anti-precompute.
-- ---------------------------------------------------------------------------
ALTER TABLE battle_tasks
    ADD COLUMN difficulty   VARCHAR(16) NOT NULL DEFAULT 'medium',
    ADD COLUMN last_used_at TIMESTAMPTZ,
    ADD COLUMN use_count    INT NOT NULL DEFAULT 0;

-- category was nullable in V66; the filter/binding model needs a concrete
-- category on every task, so backfill blanks to 'general' before tightening.
UPDATE battle_tasks
SET category = 'general'
WHERE category IS NULL OR length(btrim(category)) = 0;

ALTER TABLE battle_tasks
    ALTER COLUMN category SET NOT NULL;

ALTER TABLE battle_tasks
    ADD CONSTRAINT battle_task_difficulty_enum
        CHECK (difficulty IN ('easy', 'medium', 'hard')),
    ADD CONSTRAINT battle_task_category_not_blank
        CHECK (length(btrim(category)) > 0),
    ADD CONSTRAINT battle_task_use_count_non_negative
        CHECK (use_count >= 0);

-- The binding pool query filters on (status, category, difficulty, cooldown).
CREATE INDEX idx_battle_tasks_binding_pool
    ON battle_tasks (status, category, difficulty, last_used_at);

-- ---------------------------------------------------------------------------
-- battles: a challenge carries FILTERS; the task binds later.
-- ---------------------------------------------------------------------------
ALTER TABLE battles
    ADD COLUMN task_category_filter   VARCHAR(50),
    ADD COLUMN task_difficulty_filter VARCHAR(16),
    ADD COLUMN task_title_snapshot    TEXT;

-- The task and its snapshots no longer exist at challenge time.
ALTER TABLE battles
    ALTER COLUMN task_id DROP NOT NULL,
    ALTER COLUMN task_prompt_snapshot DROP NOT NULL,
    ALTER COLUMN task_rubric_snapshot DROP NOT NULL,
    ALTER COLUMN time_limit_seconds_snapshot DROP NOT NULL;

ALTER TABLE battles
    ADD CONSTRAINT battle_task_category_filter_not_blank
        CHECK (
            task_category_filter IS NULL
            OR length(btrim(task_category_filter)) > 0
        ),
    ADD CONSTRAINT battle_task_difficulty_filter_enum
        CHECK (
            task_difficulty_filter IS NULL
            OR task_difficulty_filter IN ('easy', 'medium', 'hard')
        ),
    -- The binding is all-or-nothing: either the battle carries the full task
    -- snapshot (id + title + prompt + rubric + time limit) or it carries none
    -- of it. A partial snapshot is a corrupt battle no code path may produce.
    ADD CONSTRAINT battle_task_binding_all_or_nothing
        CHECK (
            (
                task_id IS NULL
                AND task_title_snapshot IS NULL
                AND task_prompt_snapshot IS NULL
                AND task_rubric_snapshot IS NULL
                AND time_limit_seconds_snapshot IS NULL
            )
            OR
            (
                task_id IS NOT NULL
                AND task_title_snapshot IS NOT NULL
                AND task_prompt_snapshot IS NOT NULL
                AND task_rubric_snapshot IS NOT NULL
                AND time_limit_seconds_snapshot IS NOT NULL
            )
        ),
    -- Before the queue, no task may be bound: the secrecy invariant made
    -- structural. challenge_pending / accepted / reserved / declined / expired
    -- must all have task_id NULL.
    ADD CONSTRAINT battle_task_unbound_before_queue
        CHECK (
            status NOT IN (
                'challenge_pending', 'accepted', 'reserved',
                'declined', 'expired'
            )
            OR task_id IS NULL
        ),
    -- From the queue onward the task IS bound: a queued/running/judging/
    -- completed battle without a task_id is impossible.
    ADD CONSTRAINT battle_task_bound_from_queue
        CHECK (
            status NOT IN ('queued', 'running', 'judging', 'completed')
            OR task_id IS NOT NULL
        );
-- 'aborted' is deliberately absent from BOTH lists above: a battle may abort
-- before binding (pool exhausted for the requested filter) OR after binding
-- (queued but never reached a valid start), so aborted permits either shape.

-- The V66 content checks assumed NOT NULL columns; restate them nullable-aware
-- so a still-unbound battle (all snapshots NULL) does not trip them.
ALTER TABLE battles
    DROP CONSTRAINT battle_time_limit_positive,
    DROP CONSTRAINT battle_rubric_snapshot_is_array;

ALTER TABLE battles
    ADD CONSTRAINT battle_time_limit_positive
        CHECK (
            time_limit_seconds_snapshot IS NULL
            OR (
                time_limit_seconds_snapshot > 0
                AND time_limit_seconds_snapshot <= 3600
            )
        ),
    ADD CONSTRAINT battle_rubric_snapshot_is_array
        CHECK (
            task_rubric_snapshot IS NULL
            OR jsonb_typeof(task_rubric_snapshot) = 'array'
        );

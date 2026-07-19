-- V70: user-submitted battle tasks with LLM validation, quarantine and moderator
-- approval — plus the ledger change that lets validation spend the SAME judge
-- budget instead of opening a second, unbounded LLM path.
--
-- Until now the only way a task entered the pool was POST /battles/tasks/generate
-- behind get_admin_user, with source hardcoded to 'generated'. Opening submission
-- to any registered user introduces one threat the LLM validator cannot touch:
-- THE AUTHOR KNOWS THE TASK. If their own submission (or their second account's)
-- is ever bound to a battle they fight in, they are prepared in advance and the
-- Elo is real. Three mechanisms answer that, and two of them are structural and
-- live in this file:
--
--   1. Author exclusion at binding — enforced in battle_repo's five binding
--      sites, not here (it is a property of the battle/task pair, not of a row).
--   2. Quarantine — a validated user task enters 'quarantine', which the binding
--      pool admits ONLY for a battle that is already rated-ineligible. Elo cannot
--      move, so the author's preparation buys nothing.
--   3. Moderator approval — 'quarantine' -> 'ready' is a human act, and the
--      CHECK below makes it the ONLY way a non-generated task can reach 'ready'.
--
-- V66-V69 are FROZEN; every change here is additive.

-- ---------------------------------------------------------------------------
-- Guard 1: the approval CHECK must not be grandfathered.
--
-- battle_task_ready_requires_approval says a non-'generated' task may only be
-- 'ready' if a moderator approved it. If any existing row already violates that
-- (a 'company' task seeded straight to 'ready'), fail LOUD rather than let ALTER
-- TABLE ... ADD CONSTRAINT reject the migration with an opaque message, or worse,
-- tempt a future NOT VALID escape hatch that leaves an unapproved task bindable.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM battle_tasks
        WHERE source <> 'generated'
          AND status = 'ready'
    ) THEN
        RAISE EXCEPTION
            'V70 precondition failed: % non-generated task(s) are already '
            'status=ready with no approver; approve or retire them before '
            'migrating (the new CHECK makes moderator approval the only path)',
            (SELECT COUNT(*) FROM battle_tasks
             WHERE source <> 'generated' AND status = 'ready');
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- battle_tasks: submission source, validation lifecycle, dedup key.
-- ---------------------------------------------------------------------------

-- 'user' joins the source vocabulary. CHECKs cannot be extended in place, so the
-- V66 constraint is dropped and restated with the new member.
ALTER TABLE battle_tasks
    DROP CONSTRAINT battle_task_source_enum;

ALTER TABLE battle_tasks
    ADD CONSTRAINT battle_task_source_enum
        CHECK (source IN ('generated', 'company', 'user'));

-- The submission lifecycle: pending_validation -> (quarantine | rejected), then
-- quarantine -> ready by a moderator. 'draft'/'ready'/'retired' keep their V66
-- meaning so nothing existing has to move.
ALTER TABLE battle_tasks
    DROP CONSTRAINT battle_task_status_enum;

ALTER TABLE battle_tasks
    ADD CONSTRAINT battle_task_status_enum
        CHECK (status IN (
            'draft', 'ready', 'retired',
            'pending_validation', 'quarantine', 'rejected'
        ));

ALTER TABLE battle_tasks
    -- The validator's structured verdict, stored whole. JSONB (not a set of
    -- columns) because it is an LLM artefact for a human moderator to read, not
    -- something any query predicates on — freezing its shape into DDL would
    -- mean a migration every time the rubric of the rubric changes.
    ADD COLUMN validation_verdict JSONB,
    -- The single human-readable reason shown to the submitter. Denormalised out
    -- of validation_verdict on purpose: a rejection reason is also written by
    -- the cheap pre-LLM filters and by a moderator, neither of which produces a
    -- verdict document.
    ADD COLUMN validation_reason TEXT,
    ADD COLUMN approved_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN approved_at TIMESTAMPTZ,
    -- How many unrated battles this task has served while in quarantine. The
    -- moderator queue shows it, so approval is a decision with evidence (an
    -- anomalous winrate over N quarantine battles is the collusion signal
    -- mechanism 1 cannot catch) rather than a guess from reading the prompt.
    ADD COLUMN quarantine_battles INT NOT NULL DEFAULT 0,
    -- The canonical content key for dedup. GENERATED, not a plain column the
    -- application fills: battle_repo already keys the pool count, the candidate
    -- pick and the retire predicate on EXACTLY this expression
    -- (_CONTENT_KEY / _CONTENT_KEY_T / _CONTENT_KEY_C), and a hand-maintained
    -- copy would drift the moment one INSERT path forgot it — at which point the
    -- unique index below silently stops deduping. Generating it makes drift
    -- impossible and backfills every existing row in this same statement, so no
    -- separate UPDATE is needed. The expression is immutable (lower/btrim/
    -- regexp_replace with constant arguments), which is what STORED requires.
    ADD COLUMN content_key TEXT
        GENERATED ALWAYS AS
            (regexp_replace(btrim(lower(prompt)), '\s+', ' ', 'g')) STORED;

ALTER TABLE battle_tasks
    ADD CONSTRAINT battle_task_quarantine_battles_non_negative
        CHECK (quarantine_battles >= 0),
    -- Approval is all-or-nothing: an approver without a timestamp (or the
    -- reverse) is a half-written moderation act no path may produce.
    ADD CONSTRAINT battle_task_approval_all_or_nothing
        CHECK ((approved_by_user_id IS NULL) = (approved_at IS NULL)),
    -- THE structural anti-cheat. A task that did not come from the admin
    -- generator cannot sit in the rated pool ('ready') unless a moderator put it
    -- there. Code can forget a branch; this cannot. Note it deliberately keys on
    -- source <> 'generated' rather than source = 'user', so a future
    -- 'company' submission path inherits the same gate for free.
    ADD CONSTRAINT battle_task_ready_requires_approval
        CHECK (
            source = 'generated'
            OR status <> 'ready'
            OR approved_by_user_id IS NOT NULL
        );

-- ---------------------------------------------------------------------------
-- Guard 2: dedup uniqueness must not be grandfathered either.
--
-- SCOPE, and why it is narrower than "every non-rejected task". Duplicate
-- content among ADMIN-GENERATED tasks is not a defect — it is a tolerated,
-- designed-for condition. V67 counts COUNT(DISTINCT content_key) rather than
-- COUNT(*), picks candidates with DISTINCT ON (content_key), and retires every
-- duplicate-content SIBLING on bind, precisely because seeding produces
-- duplicates. A live pool today holds five ready, secret, unused 'generated'
-- rows sharing one key. A blanket unique index would therefore contradict an
-- existing invariant AND force an operator to delete seeded rows just to
-- migrate — patching the data to fit a constraint the feature does not need.
--
-- What dedup actually has to stop is a duplicate SUBMISSION, including two
-- concurrent submissions of the same text racing past the validator's read.
-- That is exactly what this index covers. Cross-source duplication (a user
-- submitting the text of an existing generated task) is caught by the
-- validator's cheap filter, which queries ALL non-rejected rows before spending
-- an LLM call. Residual, stated plainly: two submissions racing against a
-- GENERATED twin can still both land, because no index spans that pair. The
-- consequence is bounded — the pool already tolerates duplicate content, and
-- neither row can rate until a moderator approves it.
--
-- Rejected rows are outside both the guard and the index: a rejected submission
-- is dead and must never block a corrected resubmission.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    dupes INT;
BEGIN
    SELECT COUNT(*) INTO dupes FROM (
        SELECT content_key
        FROM battle_tasks
        WHERE source = 'user'
          AND status <> 'rejected'
        GROUP BY content_key
        HAVING COUNT(*) > 1
    ) d;
    IF dupes > 0 THEN
        RAISE EXCEPTION
            'V70 precondition failed: % duplicate content key(s) among '
            'non-rejected user submissions; reject the duplicates before '
            'migrating (submission dedup cannot grandfather them)', dupes;
    END IF;
END $$;

CREATE UNIQUE INDEX idx_battle_tasks_user_content_key_unique
    ON battle_tasks (content_key)
    WHERE source = 'user' AND status <> 'rejected';

-- The moderator queue: everything awaiting validation or approval, oldest-first
-- pressure visible via created_at. Partial, because the queue is a handful of
-- rows against a table whose other reads are all pool queries.
CREATE INDEX idx_battle_tasks_moderation_queue
    ON battle_tasks (status, created_at DESC)
    WHERE status IN ('pending_validation', 'quarantine');

-- A submitter reading "my submissions", and the daily submission quota, both
-- filter by author and date.
CREATE INDEX idx_battle_tasks_submitter
    ON battle_tasks (created_by_user_id, created_at DESC)
    WHERE source = 'user';

-- ---------------------------------------------------------------------------
-- battle_judge_call_ledger: one budget, two kinds of spend.
--
-- Task validation is an LLM call the platform pays for, so it must consume the
-- SAME daily counters the judge panel does — otherwise "the judge budget is
-- exhausted" would still leave an unbounded validation path open, and the global
-- cap would stop being a cap. Rather than a second ledger with a second set of
-- counters (two mechanisms that must be kept in agreement forever), the existing
-- ledger becomes kind-discriminated.
--
-- The four judge-only columns lose their NOT NULLs because a validation call has
-- no battle, no judge run and no pair of owners — it has one submitter. The
-- variant CHECK below restores exactly the guarantees the NOT NULLs gave, per
-- kind, so a judge row is still structurally impossible to write half-formed.
-- ---------------------------------------------------------------------------
ALTER TABLE battle_judge_call_ledger
    -- DEFAULT 'judge' is what backfills every existing row: they are all judge
    -- calls by construction (this is the only kind that existed). Guard 3 below
    -- proves that rather than assuming it.
    ADD COLUMN kind TEXT NOT NULL DEFAULT 'judge',
    ADD COLUMN submitter_user_id UUID REFERENCES users(id) ON DELETE RESTRICT;

ALTER TABLE battle_judge_call_ledger
    ALTER COLUMN battle_id DROP NOT NULL,
    ALTER COLUMN judge_run_id DROP NOT NULL,
    ALTER COLUMN owner_a_user_id DROP NOT NULL,
    ALTER COLUMN owner_b_user_id DROP NOT NULL;

ALTER TABLE battle_judge_call_ledger
    ADD CONSTRAINT battle_judge_call_kind_enum
        CHECK (kind IN ('judge', 'validation')),
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
        );

-- ---------------------------------------------------------------------------
-- Guard 3: prove the DEFAULT backfill landed correctly instead of trusting it.
--
-- Every pre-V70 row must now be kind='judge' AND satisfy the variant CHECK. The
-- CHECK was added above without NOT VALID, so PostgreSQL already validated the
-- whole table — but that failure would read as a generic constraint violation.
-- This asserts the specific, intended post-condition so a broken backfill is
-- named as such and the migration cannot report success on bad data.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    misfiled INT;
BEGIN
    SELECT COUNT(*) INTO misfiled
    FROM battle_judge_call_ledger
    WHERE kind <> 'judge'
       OR battle_id IS NULL
       OR judge_run_id IS NULL
       OR owner_a_user_id IS NULL
       OR owner_b_user_id IS NULL
       OR submitter_user_id IS NOT NULL;
    IF misfiled > 0 THEN
        RAISE EXCEPTION
            'V70 backfill failed: % pre-existing judge ledger row(s) did not '
            'land as well-formed kind=judge', misfiled;
    END IF;
END $$;

-- The validation quota reads "how many validation calls has this submitter
-- reserved today"; without this it is a seq scan over the whole judge ledger.
CREATE INDEX idx_battle_judge_calls_validation
    ON battle_judge_call_ledger (submitter_user_id, budget_day)
    WHERE kind = 'validation';

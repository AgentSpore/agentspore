-- V78: a fourth ledger shape for the task harvester's own drafting call.
--
-- 'judge' needs a battle and both owners; 'validation' needs a submitter;
-- 'auto' needs a battle and run but no owners. The harvester's drafting call
-- has NONE of these — there is no battle, no run, no submitting user, only a
-- topic pulled from an open source — so it fits no existing shape. Rather than
-- stretch 'validation' onto a synthetic submitter (which would misattribute
-- the spend to a real account's daily quota), it gets its own shape: every
-- identifying column NULL. It still consumes the GLOBAL daily counter, so the
-- one cap that matters — total provider spend per day — is not bypassed.
ALTER TABLE battle_judge_call_ledger
    DROP CONSTRAINT battle_judge_call_kind_enum,
    DROP CONSTRAINT battle_judge_call_kind_shape;

ALTER TABLE battle_judge_call_ledger
    ADD CONSTRAINT battle_judge_call_kind_enum
        CHECK (kind IN ('judge', 'validation', 'auto', 'harvest')),
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
            OR
            (
                kind = 'harvest'
                AND battle_id IS NULL
                AND judge_run_id IS NULL
                AND owner_a_user_id IS NULL
                AND owner_b_user_id IS NULL
                AND submitter_user_id IS NULL
            )
        );

-- V75: a contender runs as an AGENT, not as a single model call.
--
-- V66-V74 are FROZEN; every change here is additive.
--
-- Until now a contender was a model plus a system prompt, and answering meant
-- one HTTP call whose text became the final submission. Nothing about HOW the
-- answer was reached existed, because nothing produced it: there was no step,
-- no tool, no draft — only the last token of a single completion.
--
-- An agentic contender is the same pair (model, approach) given a sandbox and
-- the toolset agent-runner already ships. What changes is not the identity of a
-- contender but the shape of its work, so this is a column on the existing
-- table rather than a second table: a contender that fights as an agent is the
-- same row that used to fight as a call, and its Elo history stays attached to
-- it. Splitting the table would have split the rating with it.

ALTER TABLE battle_contenders
    -- 'model'  — one completion, the pre-V75 behaviour, kept so the mode is an
    --            explicit value rather than something inferred from NULLs.
    -- 'agent'  — a sandboxed agent with tools; its path is recorded as ordinary
    --            battle_submissions rows.
    ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(10) NOT NULL DEFAULT 'model',

    -- Hard ceiling on agent turns for one answer. The drive budget already
    -- bounds WALL CLOCK (ANSWER_DRIVE_BUDGET_SECONDS, 560s < the 600s battle
    -- deadline), but an agent that loops cheaply can burn the whole budget
    -- without producing anything, and the battle then voids on time rather than
    -- on the real cause. A step ceiling makes that failure legible.
    ADD COLUMN IF NOT EXISTS max_steps INT NOT NULL DEFAULT 12;

-- DROP-then-ADD rather than a bare ADD: the ADD COLUMNs above are idempotent and
-- this must be too, or a re-run dies on the constraint while reporting the
-- columns as already present. A half-idempotent migration that fails partway is
-- worse than one that never claimed to be repeatable.
ALTER TABLE battle_contenders
    DROP CONSTRAINT IF EXISTS battle_contender_execution_mode_enum,
    DROP CONSTRAINT IF EXISTS battle_contender_max_steps_sane;

ALTER TABLE battle_contenders
    ADD CONSTRAINT battle_contender_execution_mode_enum
        CHECK (execution_mode IN ('model', 'agent')),
    -- One step is not an agent, it is a call with extra machinery. The upper
    -- bound is not a performance guard — it is the point past which a single
    -- answer costs more provider calls than a whole battle used to.
    ADD CONSTRAINT battle_contender_max_steps_sane
        CHECK (max_steps BETWEEN 2 AND 40);

-- ---------------------------------------------------------------------------
-- Move the existing roster over.
--
-- The agentic mode REPLACES the one-call mode rather than sitting beside it, so
-- every enabled contender is migrated. The column keeps both values because the
-- runner must branch on something stated, and because a contender that misbehaves
-- as an agent can be put back without a migration.
--
-- Elo is deliberately NOT reset here. Ratings earned under one-call answers are
-- not comparable to ratings earned with tools, and a leaderboard that silently
-- mixes them measures nothing — but resetting is a DATA decision with a live
-- table behind it, and it belongs in the deploy runbook next to the backfill
-- script, where it can be inspected and rolled back. Doing it inside an additive
-- schema migration would make it invisible.
-- ---------------------------------------------------------------------------
UPDATE battle_contenders
SET execution_mode = 'agent',
    updated_at     = NOW()
WHERE enabled
  AND execution_mode = 'model';

-- The matchmaker reads this to decide whether a side needs a sandbox. Partial on
-- the agent arm: the model arm is expected to empty out, and an index over a
-- column that is one value everywhere earns nothing.
CREATE INDEX IF NOT EXISTS idx_battle_contenders_agentic
    ON battle_contenders (execution_mode)
    WHERE execution_mode = 'agent' AND enabled;

-- V74: one LLM vote per replicate seat, enforced by the database.
--
-- V66-V73 are FROZEN; every change here is additive.
--
-- V66's battle_judge_once is UNIQUE (battle_id, judge_kind, judge_ref,
-- replicate_seed). Because judge_ref is part of the key, two rows for the SAME
-- replicate seat are legal whenever they carry different refs — and the freeze
-- paths write under a ref of their own. collapse_open_replicates_to_error
-- avoided the second row by READING the existing judgements first, which is not
-- atomic with the write that follows: a concurrent insert between the two lands
-- a duplicate seat. Nothing downstream is prepared for that; the quorum test
-- `len(judgements) >= REPLICATE_COUNT` reads a count a duplicate inflates.
--
-- Scope: 'llm' only. A human vote (judge_kind='human', reserved for phase 2)
-- uses judge_ref=user_id precisely so that MANY users can vote on one seat,
-- one vote each — battle_judge_once already gives that for free. Extending
-- this index to 'human' would cap the crowd at a single voter per seat and
-- destroy the feature before it ships.
--
-- battle_judge_once STAYS as the authority for the human kind (one vote per
-- user per seat); this index is the authority for the llm kind, where it is
-- strictly narrower. NO writer names either one any more: upsert_judgement uses
-- an untargeted ON CONFLICT DO NOTHING so whichever rule applies can fire and
-- the insert still returns None. Naming a constraint there would make the OTHER
-- rule raise instead — which turns a freeze racing a vote into a failed settle.
-- Do not restore a named target.

-- ---------------------------------------------------------------------------
-- Existing rows first: a unique index refuses to build over a duplicate, and
-- this runs against a live database that has already served frozen battles.
-- The rule, stated rather than implied: within one seat KEEP THE OLDEST row and
-- delete the rest. A duplicate can only have come from a freeze racing a vote,
-- and the freeze is by construction the later writer — so the oldest row is the
-- genuine judgement and the ones dropped are redundant terminal errors. id
-- breaks a created_at tie so the choice is deterministic.
--
-- Verified against production before writing this: 243 judgement rows, 0 seats
-- with more than one llm row. The statement is expected to delete nothing; it
-- is here so that a duplicate created between that check and this deploy is
-- resolved by a stated rule instead of aborting the migration.
--
-- It runs inside a DO block only so that it can RAISE NOTICE what it removed:
-- a delete against a live table that leaves no trace in the deploy log is
-- unreviewable after the fact. The keep-oldest rule is right only while the
-- freeze is the later writer, so if it ever discards a genuine straggler vote,
-- the operator needs the seat printed to find it.
-- ---------------------------------------------------------------------------
DO $collapse_duplicate_seats$
DECLARE
    removed INT;
    seats   TEXT;
BEGIN
    WITH dropped AS (
        DELETE FROM battle_judgements dup
        USING battle_judgements keep
        WHERE dup.judge_kind = 'llm'
          AND keep.judge_kind = 'llm'
          AND dup.battle_id = keep.battle_id
          AND dup.replicate_seed = keep.replicate_seed
          AND (keep.created_at, keep.id) < (dup.created_at, dup.id)
        RETURNING dup.battle_id, dup.replicate_seed
    )
    SELECT count(*), string_agg(battle_id || '/' || replicate_seed, ', ')
      INTO removed, seats
      FROM dropped;

    IF removed > 0 THEN
        RAISE NOTICE 'V74 collapsed % duplicate llm judgement row(s), keeping the oldest per seat: %',
            removed, seats;
    END IF;
END
$collapse_duplicate_seats$;

-- judge_kind sits in the predicate, not in the key: it is constant across every
-- row the index covers, so carrying it as a column would add bytes and no
-- selectivity. The uniqueness enforced is (battle_id, judge_kind='llm',
-- replicate_seed).
CREATE UNIQUE INDEX IF NOT EXISTS uq_battle_judgements_llm_seat
    ON battle_judgements (battle_id, replicate_seed)
    WHERE judge_kind = 'llm';

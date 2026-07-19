-- V69: Judge-trust — allow the 'injection_suspected' judging stop reason.
--
-- Built on top of V66/V67/V68, all FROZEN; this change is purely additive.
--
-- Track 2 (judge-panel injection hardening) quarantines a battle whose fighter
-- submission carries judge-directed prompt-injection shapes: the panel is never
-- run and the battle completes UNRATED, reusing the existing V68
-- judging_stop_reason mechanism (which already forces is_rated=FALSE in
-- settlement). That reuse needs one new allowed value in the stop-reason enum.
--
-- The V68 CHECK is a closed set, so 'injection_suspected' must be admitted here
-- or the settlement UPDATE violates battle_judging_stop_reason_enum. No column,
-- index or data change — only the CHECK domain is widened, and only by adding a
-- value (every row that satisfied the V68 constraint still satisfies this one).
--
-- Deploy note: same as V68's terminal CHECKs — our deploy recreates the single
-- backend container (not rolling), so old and new code never run against this
-- schema at once. Widening an enum is backward-compatible regardless: old code
-- simply never writes the new value.

ALTER TABLE battles DROP CONSTRAINT battle_judging_stop_reason_enum;

ALTER TABLE battles ADD CONSTRAINT battle_judging_stop_reason_enum CHECK (
    judging_stop_reason IS NULL OR judging_stop_reason IN (
        'owner_budget_exhausted',
        'global_budget_exhausted',
        'battle_attempt_cap',
        'same_owner',
        'injection_suspected'
    )
);

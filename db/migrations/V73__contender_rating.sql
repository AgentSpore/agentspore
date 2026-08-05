-- V73: contenders get their OWN rating.
--
-- V66-V72 are FROZEN; every change here is additive.
--
-- A contender (V72) is a platform-run fighter: a model plus an approach, with
-- no owner. Elo lived only on agents.battle_elo, so every auto-battle settled
-- with elo_before == elo_after == 1200 and moved nothing — the whole point of
-- fielding one model under two approaches was unmeasurable.
--
-- The rating is SEPARATE from agents.battle_elo, and so is its gate. Agent Elo
-- is guarded by the V68 anti-Sybil rules (distinct verified owners, quotas)
-- because an owner who controls both fighters can farm rating for the price of
-- inference. A contender has no owner, so none of that abuse surface exists and
-- none of that gating applies. battles.is_rated keeps its existing meaning —
-- "counted toward AGENT Elo" — and stays FALSE for auto-battles.

ALTER TABLE battle_contenders
    ADD COLUMN IF NOT EXISTS elo    INT NOT NULL DEFAULT 1200,
    ADD COLUMN IF NOT EXISTS wins   INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS losses INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ties   INT NOT NULL DEFAULT 0;

ALTER TABLE battle_contenders
    -- Mirrors V66's agents_battle_elo_positive. The rating maths clamps to
    -- [100, 100000] before it ever reaches here, so this fires only if some
    -- future writer bypasses app.core.rating.
    ADD CONSTRAINT battle_contender_elo_positive CHECK (elo > 0),
    ADD CONSTRAINT battle_contender_counters_non_negative
        CHECK (wins >= 0 AND losses >= 0 AND ties >= 0);

-- No index for the leaderboard sort: the roster is single-digit rows and the
-- planner will seq-scan it whatever we build. Add one when the roster grows
-- past a few hundred contenders, not before.

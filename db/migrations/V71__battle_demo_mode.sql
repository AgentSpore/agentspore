-- V71: demo battle mode — a user pits their OWN agent against a platform demo
-- opponent, UNRATED, with zero human action on the demo side.
--
-- V66-V70 are FROZEN; every change here is additive.
--
-- Two columns and one seeded opponent:
--   * battles.is_demo — a battle whose agent_b is the platform demo opponent.
--     It is the FIRST rule in BattleService._decide_rated_eligibility (reason
--     'demo'), so a demo battle can never move Elo, and it is what the
--     reconciler keys the auto-drive (auto-accept, ready-ACK, answer) on. It is
--     the whole rating suppression: the rated path is otherwise untouched.
--   * agents.is_demo_opponent — the platform-owned sparring agent the demo
--     endpoint challenges. The endpoint resolves the opponent by this flag, not
--     by a hardcoded id, so reseeding under a new id stays safe.

ALTER TABLE battles
    ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS is_demo_opponent BOOLEAN NOT NULL DEFAULT FALSE;

-- At most ONE demo opponent, enforced by schema rather than by the seed alone. A
-- partial unique index over the flag makes a second is_demo_opponent=TRUE row
-- impossible by construction, so get_demo_opponent's "pick one" lookup is
-- deterministic and no reseed can fan out to two opponents. It does NOT constrain
-- ordinary agents: every is_demo_opponent=FALSE row is outside the partial index,
-- so any number of normal agents coexist.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_single_demo_opponent
    ON agents (is_demo_opponent)
    WHERE is_demo_opponent = TRUE;

-- 'demo' joins the rated-ineligibility reason vocabulary: EVERY demo battle
-- records it at acceptance (the first rule in _decide_rated_eligibility), so the
-- V68 CHECK must admit it. A CHECK cannot be extended in place — drop and re-add
-- with the new member, preserving the existing six.
ALTER TABLE battles DROP CONSTRAINT IF EXISTS battle_rated_reason_enum;
ALTER TABLE battles ADD CONSTRAINT battle_rated_reason_enum CHECK (
    rated_ineligibility_reason IS NULL OR rated_ineligibility_reason IN (
        'same_owner',
        'owner_daily_quota',
        'owner_concurrent_quota',
        'account_too_new',
        'account_unverified',
        'legacy',
        'demo'
    )
);

-- Seed exactly one demo opponent, owned by the earliest admin. Idempotent:
-- ON CONFLICT on the fixed id makes a re-run a no-op, and the sub-select over
-- the admin makes a database with no admin yet (a fresh migration) simply seed
-- nothing — the demo endpoint then reports "no demo opponent configured" until
-- an admin exists, rather than the migration failing. available_for_battles is
-- TRUE so the agent passes the same eligibility predicate every fighter does.
--
-- Wrapped in a column-existence guard so the migration chain applies cleanly on
-- a schema that has not (yet) grown users.is_admin — e.g. a minimal integration
-- fixture. plpgsql never plans the guarded INSERT when the branch is skipped, so
-- the reference to is_admin cannot fail there. Production carries is_admin
-- (V19), so the seed runs normally.
DO $demo_seed$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'is_admin'
    ) THEN
        INSERT INTO agents (id, handle, name, owner_user_id,
                            is_active, is_hosted, available_for_battles,
                            is_demo_opponent)
        SELECT CAST('a0000001-0000-0000-0000-000000000001' AS UUID),
               'agentspore-sparring',
               'AgentSpore Sparring',
               u.id,
               TRUE, FALSE, TRUE, TRUE
        FROM (
            SELECT id FROM users WHERE is_admin = TRUE ORDER BY created_at LIMIT 1
        ) u
        ON CONFLICT (id) DO NOTHING;
    END IF;
END $demo_seed$;

-- V68: Rated-track anti-abuse — budget caps, owner quotas, owner-level blocks.
--
-- Built on top of V66 (battle state machine) and V67 (task secrecy). Both are
-- FROZEN; every change here is additive in V68.
--
-- Track 3 answers "may this battle affect Elo, and may the platform afford to
-- judge it". Three orthogonal mechanisms:
--   1. Rating eligibility (rated_eligible / is_rated / rated_ineligibility_reason)
--      — frozen at acceptance from owner snapshots, finalized at settlement.
--   2. A judge-call spend ledger + daily counters (PostgreSQL is the
--      authoritative budget; Redis only ever holds the transient breaker).
--   3. Owner-level blocks, replacing the V66 agent-pair block table so a block
--      covers all current and future agents of either owner.
--
-- The active-pending target cap is also re-keyed here to the target OWNER and
-- narrowed to genuinely unanswered challenges, so a griefer can no longer fill
-- an owner's inbound cap with dead history.

-- ---------------------------------------------------------------------------
-- A1. Battle rating state.
-- ---------------------------------------------------------------------------
ALTER TABLE battles
    ADD COLUMN rated_eligible             BOOLEAN,
    ADD COLUMN rated_quota_day            DATE,
    ADD COLUMN rated_ineligibility_reason VARCHAR(40),
    ADD COLUMN is_rated                   BOOLEAN,
    ADD COLUMN judging_stop_reason        VARCHAR(40);

-- Backfill BEFORE the terminal CHECK: existing rows must satisfy it. Every
-- pre-V68 battle is forced UNRATED with reason 'legacy': the anti-Sybil gate
-- (verified + age >= 7d + concurrent/daily quota) did NOT exist when these
-- battles were accepted, so the migration CANNOT reconstruct whether they would
-- have earned a rated slot. Grandfathering an in-flight distinct-owner battle
-- into rated=TRUE would let it move Elo without ever passing the gate, so the
-- honest reconstruction is "eligibility was never decided under the new rules"
-- = FALSE/legacy. Fresh deploys have no rows; this only governs an upgrade.
UPDATE battles
SET rated_eligible = FALSE,
    rated_quota_day = NULL,
    rated_ineligibility_reason = CASE
        WHEN agent_b_owner_snapshot IS NULL THEN 'legacy'
        WHEN agent_a_owner_snapshot = agent_b_owner_snapshot THEN 'same_owner'
        ELSE 'legacy'
    END
WHERE status IN ('accepted', 'reserved', 'queued', 'running', 'judging', 'completed');

-- Every legacy completed battle is therefore unrated (rated_eligible = FALSE),
-- which the battle_rated_requires_eligibility CHECK below demands of is_rated.
UPDATE battles
SET is_rated = FALSE
WHERE status = 'completed';

ALTER TABLE battles ADD CONSTRAINT battle_rated_reason_enum CHECK (
    rated_ineligibility_reason IS NULL OR rated_ineligibility_reason IN (
        'same_owner',
        'owner_daily_quota',
        'owner_concurrent_quota',
        'account_too_new',
        'account_unverified',
        'legacy'
    )
);

ALTER TABLE battles ADD CONSTRAINT battle_judging_stop_reason_enum CHECK (
    judging_stop_reason IS NULL OR judging_stop_reason IN (
        'owner_budget_exhausted',
        'global_budget_exhausted',
        'battle_attempt_cap',
        'same_owner'
    )
);

-- is_rated is the final outcome, written only at judging -> completed. It exists
-- exactly on completed battles.
-- NOTE (deploy): this is a STRICT CHECK. Our deploy recreates the single backend
-- container (not a rolling deploy), so old+new code never run against it at once.
-- A rolling deploy WOULD need expand/contract (add nullable, backfill, then the
-- CHECK) to avoid old code completing a battle without writing is_rated.
ALTER TABLE battles ADD CONSTRAINT battle_is_rated_terminal CHECK (
    (status = 'completed') = (is_rated IS NOT NULL)
);

-- A battle can only END rated if it reserved a rated slot at acceptance.
ALTER TABLE battles ADD CONSTRAINT battle_rated_requires_eligibility CHECK (
    is_rated IS DISTINCT FROM TRUE OR rated_eligible = TRUE
);

-- A rated outcome needs a winner (a no-quorum verdict is never rated).
ALTER TABLE battles ADD CONSTRAINT battle_rated_requires_verdict CHECK (
    is_rated IS DISTINCT FROM TRUE OR winner IS NOT NULL
);

-- A reserved rated slot always names the day it was reserved on.
ALTER TABLE battles ADD CONSTRAINT battle_rated_quota_day_required CHECK (
    rated_eligible IS DISTINCT FROM TRUE OR rated_quota_day IS NOT NULL
);

-- Owner-keyed rated-slot lookups: daily quota and active-concurrent quota, both
-- keyed on each owner side. Partial on rated_eligible so unrated battles never
-- enter these indexes.
CREATE INDEX idx_battles_owner_a_rated_day
    ON battles (agent_a_owner_snapshot, rated_quota_day)
    WHERE rated_eligible = TRUE;

CREATE INDEX idx_battles_owner_b_rated_day
    ON battles (agent_b_owner_snapshot, rated_quota_day)
    WHERE rated_eligible = TRUE;

CREATE INDEX idx_battles_active_rated_a
    ON battles (agent_a_owner_snapshot, status)
    WHERE rated_eligible = TRUE
      AND status IN ('accepted','reserved','queued','running','judging');

CREATE INDEX idx_battles_active_rated_b
    ON battles (agent_b_owner_snapshot, status)
    WHERE rated_eligible = TRUE
      AND status IN ('accepted','reserved','queued','running','judging');

-- ---------------------------------------------------------------------------
-- A2. Judge-call ledger and daily counters.
--
-- A "call unit" is one attempted provider HTTP request, INCLUDING a timeout or
-- unknown result. It is reserved before transmitting: a crash after reservation
-- still consumes the unit because billing status is unknown. PostgreSQL is the
-- authoritative budget — Redis eviction/restart must never reopen spend.
-- ---------------------------------------------------------------------------
CREATE TABLE battle_judge_call_ledger (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    battle_id           UUID NOT NULL REFERENCES battles(id) ON DELETE CASCADE,
    judge_run_id        UUID NOT NULL REFERENCES battle_judge_runs(id) ON DELETE CASCADE,
    owner_a_user_id     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    owner_b_user_id     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    budget_day          DATE NOT NULL,
    provider_attempt_no INT NOT NULL,
    provider            VARCHAR(40) NOT NULL,
    model               VARCHAR(120) NOT NULL,
    status              VARCHAR(12) NOT NULL DEFAULT 'reserved',
    http_status         INT,
    error_class         VARCHAR(80),
    reserved_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,

    CONSTRAINT battle_judge_call_attempt_positive
        CHECK (provider_attempt_no > 0),
    CONSTRAINT battle_judge_call_status_enum
        CHECK (status IN ('reserved','succeeded','failed')),
    -- A reserved row has no finish time; a settled one has both a terminal
    -- status and a finish time.
    CONSTRAINT battle_judge_call_finished_agrees
        CHECK ((status = 'reserved') = (finished_at IS NULL)),
    -- One provider attempt number per raw judge-run: a duplicate cannot
    -- double-increment the counters.
    UNIQUE (judge_run_id, provider_attempt_no)
);

CREATE INDEX idx_battle_judge_calls_battle
    ON battle_judge_call_ledger (battle_id, reserved_at);

CREATE INDEX idx_battle_judge_calls_failures
    ON battle_judge_call_ledger (reserved_at)
    WHERE status = 'failed';

CREATE TABLE battle_judge_global_daily_usage (
    budget_day     DATE PRIMARY KEY,
    reserved_calls INT NOT NULL DEFAULT 0 CHECK (reserved_calls >= 0)
);

CREATE TABLE battle_judge_owner_daily_usage (
    budget_day     DATE NOT NULL,
    owner_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    reserved_calls INT NOT NULL DEFAULT 0 CHECK (reserved_calls >= 0),
    PRIMARY KEY (budget_day, owner_user_id)
);

-- ---------------------------------------------------------------------------
-- A3. Owner-level blocks replace the V66 agent-pair block table.
--
-- A block covers all current and future agents of either owner, so blocking is
-- keyed on the frozen user id, not the mutable agent id.
-- ---------------------------------------------------------------------------
ALTER TABLE battle_blocks RENAME TO battle_agent_blocks_legacy;

CREATE TABLE battle_blocks (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    blocker_owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blocked_owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT battle_block_distinct_owners
        CHECK (blocker_owner_user_id <> blocked_owner_user_id),
    UNIQUE (blocker_owner_user_id, blocked_owner_user_id)
);

CREATE INDEX idx_battle_blocks_blocked_owner
    ON battle_blocks (blocked_owner_user_id);

-- Backfill legacy agent-pair blocks by resolving both agents to their current
-- non-null owners. Rows whose agent lost its owner, or that resolve to the same
-- owner (self-block is illegal at the owner grain), are dropped. ON CONFLICT
-- collapses multiple agent pairs that map to the same owner pair.
INSERT INTO battle_blocks (blocker_owner_user_id, blocked_owner_user_id, created_at)
SELECT ba.owner_user_id, bb.owner_user_id, l.created_at
FROM battle_agent_blocks_legacy l
JOIN agents ba ON ba.id = l.blocker_agent_id
JOIN agents bb ON bb.id = l.blocked_agent_id
WHERE ba.owner_user_id IS NOT NULL
  AND bb.owner_user_id IS NOT NULL
  AND ba.owner_user_id <> bb.owner_user_id
ON CONFLICT (blocker_owner_user_id, blocked_owner_user_id) DO NOTHING;

-- destructive: the legacy agent-pair block table is fully superseded by the
-- owner-level table above and its rows have been backfilled into it; drop it
-- only after the backfill, in the same migration.
DROP TABLE battle_agent_blocks_legacy;

-- ---------------------------------------------------------------------------
-- E. Active-pending target-cap fix.
--
-- The V66 cap counted every historical row in a time window; a griefer could
-- fill it with expired/declined rows. Re-key to the target owner and count only
-- genuinely unanswered challenges (challenge_expires_at is the authoritative
-- active-pending boundary).
-- ---------------------------------------------------------------------------
CREATE INDEX idx_battles_target_owner_active_pending
    ON battles (agent_b_owner_snapshot, challenge_expires_at)
    WHERE status = 'challenge_pending';

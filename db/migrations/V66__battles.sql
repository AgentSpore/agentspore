-- V66: Agent battles — tasks, duels, reservations, submissions, judging.
--
-- Built on top of V65 (agent_events). V65 owns "did this event reach THIS
-- agent, and did the agent confirm it". V66 owns "is this battle allowed to
-- move from state X to state Y" — and the two are deliberately not the same
-- question. A row in agent_events reaching status 'acked' is NOT battle
-- readiness: mark_acked() never looks at the event type and never calls
-- battle code. Readiness is a conclusion this schema lets battle_service
-- draw, by pinning each readiness attempt to the EXACT event_ids of the
-- CURRENT generation (readiness_generation, ready_check_event_id_a/b).
--
-- The correctness boundary is the database, not the Redis leader lease.
-- Every transition is a compare-and-set that names the expected old state
-- and returns the changed row -- see battle_repo.py. The scheduler lease is
-- operational throttling only: losing leadership does not stop an in-flight
-- run_once(), so a former leader and a new leader can both be executing.
-- Per-row lease tokens (lease_token/lease_expires_at) decide who may finish
-- a unit of work, not who is leader.
--
-- Battle lifecycle:
--   challenge_pending  challenge created, awaiting B's owner consent
--   accepted           B's owner consented (JWT). Consent has no transport
--                      meaning: B may be offline right now, and that is fine
--   reserved           both fighters hold a battle_reservations row and a
--                      ready-check event was armed for this generation
--   queued             both sides ready-ACKed the exact current-generation
--                      events inside the lease -- the only path to a battle
--   running            started_at/deadline_at fixed, battle_turn dispatched
--   judging            deadline passed or both sides submitted final
--   completed          verdict applied (terminal, winner may be NULL)
--   declined           B's owner refused (terminal)
--   expired            challenge or readiness lapsed (terminal)
--   aborted            never reached a valid shared start (terminal)
--
-- 'aborted' is reserved for battles that never reached 'running'. A battle
-- that reached 'running' always reconciles to judging/completed with
-- whatever accumulated by the deadline -- an agent going silent produces a
-- truncated submission, never a retroactive abort.

-- ---------------------------------------------------------------------------
-- battle_tasks: what the fighters are asked to do.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS battle_tasks (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source             VARCHAR(20) NOT NULL,
    org_id             UUID,
    title              TEXT NOT NULL,
    prompt             TEXT NOT NULL,
    rubric             JSONB NOT NULL,
    category           VARCHAR(50),
    time_limit_seconds INT NOT NULL DEFAULT 600,
    status             VARCHAR(20) NOT NULL DEFAULT 'ready',
    created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT battle_task_source_enum
        CHECK (source IN ('generated', 'company')),
    CONSTRAINT battle_task_status_enum
        CHECK (status IN ('draft', 'ready', 'retired')),
    -- Judges score against the rubric, so it has to be a list of criteria.
    -- A scalar or object here would silently produce vibes-based judging.
    CONSTRAINT battle_task_rubric_is_array
        CHECK (jsonb_typeof(rubric) = 'array'),
    CONSTRAINT battle_task_title_not_blank
        CHECK (length(btrim(title)) > 0),
    CONSTRAINT battle_task_prompt_not_blank
        CHECK (length(btrim(prompt)) > 0),
    -- Same bound as battles.time_limit_seconds_snapshot: a task that cannot
    -- be snapshotted into a legal battle must not be storable in the first
    -- place.
    CONSTRAINT battle_task_time_limit_bounded
        CHECK (time_limit_seconds > 0 AND time_limit_seconds <= 3600)
);

CREATE INDEX IF NOT EXISTS idx_battle_tasks_status
    ON battle_tasks (status, created_at DESC);

-- ---------------------------------------------------------------------------
-- battles: the duel itself and its state machine.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS battles (
    id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL REFERENCES battle_tasks(id) ON DELETE RESTRICT,
    status  VARCHAR(20) NOT NULL DEFAULT 'challenge_pending',

    -- agent_b_id is NULLABLE on purpose: NULL means an open challenge that
    -- any eligible agent may claim atomically -- an UPDATE that sets
    -- agent_b_id only while it IS NULL and the status is still
    -- challenge_pending, returning the row (battle_repo.claim_open_challenge).
    -- An empty result means the candidate lost the race. Claiming is not
    -- consent: B's owner still has to accept.
    agent_a_id UUID NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    agent_b_id UUID REFERENCES agents(id) ON DELETE RESTRICT,

    -- Ownership is snapshotted because agents.owner_user_id is mutable and
    -- nullable. Reward and rating eligibility are decided by these frozen
    -- values, never by the agent's current state.
    agent_a_owner_snapshot UUID NOT NULL,
    agent_b_owner_snapshot UUID,

    -- Fact 1 of four: owner consent. Independent of transport, and of
    -- whether the agent is reachable at this instant.
    agent_b_accepted_at   TIMESTAMPTZ,
    challenge_expires_at  TIMESTAMPTZ NOT NULL,

    -- Fact 4 of four: battle readiness. Bound to EXACT event ids of the
    -- CURRENT generation, because a generic 'acked' row proves nothing about
    -- this battle: the generation is bumped on every re-arm so a late ACK
    -- from a previous attempt can never satisfy the present one.
    ready_lease_expires_at TIMESTAMPTZ,
    readiness_generation   INT NOT NULL DEFAULT 0,
    ready_check_event_id_a UUID REFERENCES agent_events(event_id) ON DELETE SET NULL,
    ready_check_event_id_b UUID REFERENCES agent_events(event_id) ON DELETE SET NULL,

    -- Frozen at challenge time, never updated. Editing the live battle_tasks
    -- row must not change what the fighters were asked or what the judges
    -- score against -- for any battle, at any stage.
    task_prompt_snapshot        TEXT NOT NULL,
    task_rubric_snapshot        JSONB NOT NULL,
    time_limit_seconds_snapshot INT NOT NULL,

    winner         VARCHAR(10),
    verdict_reason TEXT,
    elo_a_before INT,
    elo_b_before INT,
    elo_a_after  INT,
    elo_b_after  INT,

    -- Per-row processing claim. The scheduler leader lease cannot fence this:
    -- losing leadership only logs, it does not stop an in-flight run_once().
    -- A worker writes a terminal result only while it still owns the token.
    lease_token         UUID,
    lease_expires_at    TIMESTAMPTZ,
    lease_attempt_count INT NOT NULL DEFAULT 0,

    challenged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    queued_at     TIMESTAMPTZ,
    started_at    TIMESTAMPTZ,
    deadline_at   TIMESTAMPTZ,
    finalized_at  TIMESTAMPTZ,
    ended_at      TIMESTAMPTZ,

    CONSTRAINT battle_status_enum CHECK (status IN
        ('challenge_pending', 'accepted', 'reserved', 'queued', 'running',
         'judging', 'completed', 'declined', 'expired', 'aborted')),
    CONSTRAINT battle_winner_enum
        CHECK (winner IS NULL OR winner IN ('a', 'b', 'tie')),
    CONSTRAINT battle_distinct_agents
        CHECK (agent_a_id <> agent_b_id),

    -- A winner may only exist on a completed battle. 'completed' with a NULL
    -- winner stays legal: that is the no-quorum verdict, which must never be
    -- forced into inventing a side.
    CONSTRAINT battle_winner_only_when_completed
        CHECK (winner IS NULL OR status = 'completed'),
    CONSTRAINT battle_finalized_iff_completed
        CHECK ((status = 'completed') = (finalized_at IS NOT NULL)),
    CONSTRAINT battle_ended_iff_terminal
        CHECK ((status IN ('completed', 'declined', 'expired', 'aborted'))
               = (ended_at IS NOT NULL)),

    -- Only an open challenge may lack an opponent. Any state past the claim
    -- (including 'declined', which needs B's owner to refuse) requires one.
    CONSTRAINT battle_opponent_required_past_claim
        CHECK (agent_b_id IS NOT NULL
               OR status IN ('challenge_pending', 'expired', 'aborted')),
    CONSTRAINT battle_consent_requires_opponent
        CHECK (agent_b_accepted_at IS NULL OR agent_b_id IS NOT NULL),
    CONSTRAINT battle_owner_snapshot_requires_opponent
        CHECK (agent_b_owner_snapshot IS NULL OR agent_b_id IS NOT NULL),

    CONSTRAINT battle_time_limit_positive
        CHECK (time_limit_seconds_snapshot > 0
               AND time_limit_seconds_snapshot <= 3600),
    CONSTRAINT battle_rubric_snapshot_is_array
        CHECK (jsonb_typeof(task_rubric_snapshot) = 'array'),
    CONSTRAINT battle_readiness_generation_non_negative
        CHECK (readiness_generation >= 0),
    CONSTRAINT battle_lease_attempt_count_non_negative
        CHECK (lease_attempt_count >= 0),
    CONSTRAINT battle_lease_token_has_expiry
        CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL)),
    CONSTRAINT battle_elo_positive
        CHECK ((elo_a_before IS NULL OR elo_a_before > 0)
               AND (elo_b_before IS NULL OR elo_b_before > 0)
               AND (elo_a_after IS NULL OR elo_a_after > 0)
               AND (elo_b_after IS NULL OR elo_b_after > 0)),

    -- deadline_at is THE wall clock, and it only exists once a shared start
    -- exists. Comparisons against NULL yield NULL, which a CHECK accepts --
    -- so each of these only bites once both sides are set.
    CONSTRAINT battle_deadline_requires_start
        CHECK (deadline_at IS NULL OR started_at IS NOT NULL),
    CONSTRAINT battle_deadline_after_start
        CHECK (deadline_at > started_at),
    CONSTRAINT battle_queued_after_challenged
        CHECK (queued_at >= challenged_at),
    CONSTRAINT battle_started_after_queued
        CHECK (started_at >= queued_at),
    CONSTRAINT battle_ended_after_challenged
        CHECK (ended_at >= challenged_at)
);

CREATE INDEX IF NOT EXISTS idx_battles_status_queued
    ON battles (status, queued_at);
CREATE INDEX IF NOT EXISTS idx_battles_challenge_expiry
    ON battles (status, challenge_expires_at)
    WHERE status = 'challenge_pending';
-- Readiness reaper: reserved battles whose ready lease lapsed.
CREATE INDEX IF NOT EXISTS idx_battles_ready_lease_expiry
    ON battles (ready_lease_expires_at)
    WHERE status = 'reserved';
-- Deadline reconciliation: running battles past their wall clock.
CREATE INDEX IF NOT EXISTS idx_battles_deadline
    ON battles (deadline_at)
    WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_battles_agent_a ON battles (agent_a_id);
CREATE INDEX IF NOT EXISTS idx_battles_agent_b ON battles (agent_b_id);

-- ---------------------------------------------------------------------------
-- battle_reservations: one agent, one active battle.
--
-- A table, not two partial unique indexes on battles.agent_a_id/agent_b_id:
-- those are two separate indexes over two separate columns, so they happily
-- allow agent X to be side A in battle 1 while being side B in battle 2 --
-- exactly the double-spend of the owner's LLM key we are preventing. A
-- PRIMARY KEY on a role-independent agent_id is one cross-role namespace,
-- so the conflict is guaranteed however the agent participates.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS battle_reservations (
    agent_id       UUID PRIMARY KEY REFERENCES agents(id) ON DELETE RESTRICT,
    battle_id      UUID NOT NULL REFERENCES battles(id) ON DELETE CASCADE,
    reserved_until TIMESTAMPTZ NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT battle_reservation_future
        CHECK (reserved_until > created_at)
);

CREATE INDEX IF NOT EXISTS idx_battle_reservations_battle
    ON battle_reservations (battle_id);
CREATE INDEX IF NOT EXISTS idx_battle_reservations_expiry
    ON battle_reservations (reserved_until);

-- ---------------------------------------------------------------------------
-- Abuse controls. A challenge spends the target owner's inference budget,
-- so the target must opt in and keep the ability to say never again.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS battle_blocks (
    blocker_agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    blocked_agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (blocker_agent_id, blocked_agent_id),
    CONSTRAINT battle_block_distinct_agents
        CHECK (blocker_agent_id <> blocked_agent_id)
);

CREATE TABLE IF NOT EXISTS battle_challenge_cooldowns (
    challenger_agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    target_agent_id     UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    cooldown_until      TIMESTAMPTZ NOT NULL,

    PRIMARY KEY (challenger_agent_id, target_agent_id),
    CONSTRAINT battle_cooldown_distinct_agents
        CHECK (challenger_agent_id <> target_agent_id)
);

CREATE INDEX IF NOT EXISTS idx_battle_cooldowns_expiry
    ON battle_challenge_cooldowns (cooldown_until);

-- ---------------------------------------------------------------------------
-- battle_submissions: checkpoints, not just a final answer.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS battle_submissions (
    battle_id   UUID NOT NULL REFERENCES battles(id) ON DELETE CASCADE,
    side        VARCHAR(1) NOT NULL,
    seq_no      INT NOT NULL DEFAULT 0,
    content     TEXT,
    tokens_used INT,
    is_final    BOOLEAN NOT NULL DEFAULT FALSE,
    -- Server clock only. A client-supplied timestamp would let a fighter
    -- backdate a submission past deadline_at.
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    truncated   BOOLEAN NOT NULL DEFAULT FALSE,
    -- Exception TYPE only, never a value: a message could carry the
    -- fighter's key material or prompt into our tables.
    error       VARCHAR(80),

    PRIMARY KEY (battle_id, side, seq_no),
    CONSTRAINT battle_submission_side_enum
        CHECK (side IN ('a', 'b')),
    CONSTRAINT battle_submission_seq_no_non_negative
        CHECK (seq_no >= 0),
    CONSTRAINT battle_submission_tokens_non_negative
        CHECK (tokens_used IS NULL OR tokens_used >= 0),
    CONSTRAINT battle_submission_finished_after_received
        CHECK (finished_at >= received_at)
);

-- Exactly one final row per side. A synthetic truncated submission for a
-- silent fighter takes this slot, so a late arrival cannot claim it too.
CREATE UNIQUE INDEX IF NOT EXISTS idx_battle_submissions_final
    ON battle_submissions (battle_id, side)
    WHERE is_final;

-- ---------------------------------------------------------------------------
-- battle_judge_runs: RAW judge runs -- one row per half of an ab/ba pair.
--
-- The uniqueness key includes presented_order, because one replicate is
-- deliberately TWO runs (ab and ba) by the same judge. Without
-- presented_order in the key the second half of every pair would violate
-- the constraint, and the single available model could never cast more than
-- one 'llm' run at all.
--
-- Rows carry their own lease: each raw judge run is an independently
-- claimable unit of durable work, so a worker that lost the row can never
-- write its late result (see battle_repo CAS on lease_token).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS battle_judge_runs (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    battle_id      UUID NOT NULL REFERENCES battles(id) ON DELETE CASCADE,
    judge_kind     VARCHAR(10) NOT NULL,
    judge_ref      VARCHAR(120) NOT NULL,
    -- Stable hash(battle_id, replicate_no). Persisted whether or not the
    -- provider honours it, because it is the replicate's identity here.
    replicate_seed  VARCHAR(20) NOT NULL DEFAULT '0',
    presented_order VARCHAR(3) NOT NULL,

    status           VARCHAR(12) NOT NULL DEFAULT 'pending',
    lease_token      UUID,
    lease_expires_at TIMESTAMPTZ,
    attempt_count    INT NOT NULL DEFAULT 0,

    vote       VARCHAR(10),
    confidence REAL,
    reasoning  TEXT,
    scores     JSONB,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    CONSTRAINT battle_judge_run_kind_enum
        CHECK (judge_kind IN ('llm', 'human')),
    CONSTRAINT battle_judge_run_order_enum
        CHECK (presented_order IN ('ab', 'ba')),
    CONSTRAINT battle_judge_run_status_enum
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    -- 'abstain' exists so malformed judge output has somewhere honest to
    -- land. Mapping it onto 'tie' would let a broken judge mint tie-Elo.
    CONSTRAINT battle_judge_run_vote_enum
        CHECK (vote IS NULL OR vote IN ('a', 'b', 'tie', 'abstain', 'error')),
    CONSTRAINT battle_judge_run_confidence_range
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    CONSTRAINT battle_judge_run_attempt_count_non_negative
        CHECK (attempt_count >= 0),
    CONSTRAINT battle_judge_run_lease_token_has_expiry
        CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL)),
    -- A finished run has both a verdict and a completion time, or neither.
    CONSTRAINT battle_judge_run_completed_agrees
        CHECK ((status = 'completed') = (completed_at IS NOT NULL)),
    CONSTRAINT battle_judge_run_completed_has_vote
        CHECK (status <> 'completed' OR vote IS NOT NULL),
    CONSTRAINT battle_judge_run_completed_after_created
        CHECK (completed_at >= created_at),

    CONSTRAINT battle_judge_raw_run_once
        UNIQUE (battle_id, judge_kind, judge_ref, replicate_seed, presented_order)
);

CREATE INDEX IF NOT EXISTS idx_battle_judge_runs_battle
    ON battle_judge_runs (battle_id, status);
CREATE INDEX IF NOT EXISTS idx_battle_judge_runs_lease
    ON battle_judge_runs (lease_expires_at)
    WHERE status = 'running';

-- ---------------------------------------------------------------------------
-- battle_judgements: COLLAPSED votes -- one row per replicate.
--
-- The two halves of an ab/ba pair are never two votes. Collapsing a pair is
-- a decision, made at finalization:
--   both halves agree on a side  -> that side, confidence is their mean
--   both halves say tie          -> tie
--   halves disagree A vs B       -> tie, flagged position_sensitive
--   either half errored          -> error
--   either half invalid/abstain  -> abstain
-- The unique key WITHOUT presented_order is what physically caps three paired
-- replicates at three collapsed votes -- the quorum arithmetic cannot be
-- inflated by counting the same replicate twice.
--
-- Human votes (phase 2) reuse this table with judge_kind='human',
-- judge_ref=user_id and the default replicate_seed, so the same key gives
-- one vote per user per battle for free.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS battle_judgements (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    battle_id      UUID NOT NULL REFERENCES battles(id) ON DELETE CASCADE,
    judge_kind     VARCHAR(10) NOT NULL,
    judge_ref      VARCHAR(120) NOT NULL,
    replicate_seed VARCHAR(20) NOT NULL DEFAULT '0',

    vote       VARCHAR(10) NOT NULL,
    confidence REAL,
    reasoning  TEXT,
    scores     JSONB,
    -- The pair disagreed purely by presentation order: counted in the
    -- denominator, never as a confident vote for a side.
    position_sensitive BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT battle_judgement_kind_enum
        CHECK (judge_kind IN ('llm', 'human')),
    CONSTRAINT battle_judgement_vote_enum
        CHECK (vote IN ('a', 'b', 'tie', 'abstain', 'error')),
    CONSTRAINT battle_judgement_confidence_range
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    -- position_sensitive describes an A/B split that collapsed to a tie.
    -- Flagging it on any other vote would misreport the verdict reason.
    CONSTRAINT battle_judgement_position_sensitive_is_tie
        CHECK (position_sensitive = FALSE OR vote = 'tie'),

    CONSTRAINT battle_judge_once
        UNIQUE (battle_id, judge_kind, judge_ref, replicate_seed)
);

CREATE INDEX IF NOT EXISTS idx_battle_judgements_battle
    ON battle_judgements (battle_id);

-- ---------------------------------------------------------------------------
-- Rating columns on agents. available_for_battles is opt-in and defaults to
-- FALSE: a battle spends the owner's own LLM key, so silence is a no.
--
-- The leaderboard sort index on agents(battle_elo DESC) is deliberately NOT
-- here: agents is a populated production table, so its index belongs in a
-- separate non-transactional migration rather than inside this transactional
-- V__ file. No reader needs it until the leaderboard ships (step 9/10).
-- ---------------------------------------------------------------------------
ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS battle_elo INT NOT NULL DEFAULT 1200,
    ADD COLUMN IF NOT EXISTS battle_wins INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS battle_losses INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS battle_ties INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS available_for_battles BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE agents
    ADD CONSTRAINT agents_battle_elo_positive CHECK (battle_elo > 0);
ALTER TABLE agents
    ADD CONSTRAINT agents_battle_counters_non_negative
        CHECK (battle_wins >= 0 AND battle_losses >= 0 AND battle_ties >= 0);

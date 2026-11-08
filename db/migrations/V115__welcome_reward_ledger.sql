-- Inert welcome ledger: no funding, decisions, applications or grants are seeded.
CREATE TABLE reward_programs (
    id text PRIMARY KEY CHECK (id = 'welcome'),
    enabled boolean NOT NULL DEFAULT false,
    mint text NOT NULL CHECK (mint = '5ZkjEjfDAPuSg7pRxCRJsJuZ8FByRSyAgAA8SLMMpump'),
    token_program text NOT NULL CHECK (token_program = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'),
    decimals smallint NOT NULL CHECK (decimals = 6),
    tariff_version text NOT NULL CHECK (length(tariff_version) BETWEEN 1 AND 80),
    amount_raw bigint NOT NULL CHECK (amount_raw > 0),
    admission_start timestamptz,
    admission_end timestamptz,
    CHECK (NOT enabled OR admission_start IS NOT NULL),
    CHECK (admission_end IS NULL OR admission_end > admission_start)
);
INSERT INTO reward_programs(id,mint,token_program,decimals,tariff_version,amount_raw)
VALUES ('welcome','5ZkjEjfDAPuSg7pRxCRJsJuZ8FByRSyAgAA8SLMMpump',
        'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb',6,'welcome-v1',10000000000);

CREATE TABLE reward_funding_entries (
    allocation_id uuid PRIMARY KEY,
    program_id text NOT NULL REFERENCES reward_programs(id) ON DELETE RESTRICT,
    amount_raw bigint NOT NULL CHECK (amount_raw > 0),
    evidence_ref text NOT NULL UNIQUE CHECK (length(evidence_ref) BETWEEN 1 AND 200),
    authority_ref text NOT NULL CHECK (length(authority_ref) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX reward_funding_program_idx ON reward_funding_entries(program_id);

CREATE TABLE welcome_reward_grants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    program_id text NOT NULL REFERENCES reward_programs(id) ON DELETE RESTRICT,
    owner_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    source_event uuid NOT NULL,
    state text NOT NULL DEFAULT 'application'
        CHECK (state IN ('application','frozen','available','denied')),
    beneficiary_key text CHECK (length(beneficiary_key) BETWEEN 1 AND 200),
    tariff_version text,
    amount_raw bigint CHECK (amount_raw > 0),
    decision_id bigint,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(program_id,owner_user_id),
    UNIQUE(program_id,source_event),
    UNIQUE(program_id,beneficiary_key),
    UNIQUE(id,owner_user_id,source_event),
    CHECK (source_event = owner_user_id),
    CHECK ((state IN ('application','denied') AND amount_raw IS NULL
            AND beneficiary_key IS NULL AND tariff_version IS NULL AND decision_id IS NULL)
        OR (state IN ('frozen','available') AND amount_raw IS NOT NULL
            AND beneficiary_key IS NOT NULL AND tariff_version IS NOT NULL AND decision_id IS NOT NULL))
);
CREATE INDEX welcome_reward_owner_idx ON welcome_reward_grants(owner_user_id);

CREATE TABLE reward_eligibility_decisions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    revision bigint NOT NULL CHECK (revision > 0),
    grant_id uuid NOT NULL,
    owner_user_id uuid NOT NULL,
    source_event uuid NOT NULL,
    policy_version text NOT NULL CHECK (length(policy_version) BETWEEN 1 AND 80),
    outcome text NOT NULL CHECK (outcome IN ('allow','hold','deny')),
    beneficiary_key text CHECK (length(beneficiary_key) BETWEEN 1 AND 200),
    authority_ref text NOT NULL CHECK (length(authority_ref) BETWEEN 1 AND 200),
    evidence_ref text NOT NULL UNIQUE CHECK (length(evidence_ref) BETWEEN 1 AND 200),
    email_proof_at timestamptz,
    issued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > issued_at),
    FOREIGN KEY(grant_id,owner_user_id,source_event)
        REFERENCES welcome_reward_grants(id,owner_user_id,source_event) ON DELETE RESTRICT,
    UNIQUE(id,grant_id),
    UNIQUE(grant_id,revision)
);
CREATE INDEX reward_decision_latest_idx ON reward_eligibility_decisions(grant_id,revision DESC);
ALTER TABLE welcome_reward_grants ADD CONSTRAINT welcome_reward_decision_fk
    FOREIGN KEY(decision_id,id) REFERENCES reward_eligibility_decisions(id,grant_id) ON DELETE RESTRICT;

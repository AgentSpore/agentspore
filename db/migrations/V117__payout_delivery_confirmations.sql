CREATE TABLE IF NOT EXISTS payout_delivery_confirmations (
    intent_id UUID PRIMARY KEY REFERENCES payout_delivery_attempts(intent_id),
    finalized_slot TEXT NOT NULL CHECK (CASE WHEN finalized_slot ~ '^(0|[1-9][0-9]{0,19})$'
        THEN finalized_slot::numeric <= 18446744073709551615 ELSE FALSE END),
    verifier_version INTEGER NOT NULL CHECK (verifier_version >= 1),
    evidence_digest TEXT NOT NULL CHECK (evidence_digest ~ '^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE payout_delivery_claims DROP CONSTRAINT IF EXISTS payout_delivery_claims_state_check;
ALTER TABLE payout_delivery_claims ADD CONSTRAINT payout_delivery_claims_state_check
    CHECK (state IN ('ready', 'claimed', 'prepared'));
ALTER TABLE payout_delivery_claims DROP CONSTRAINT IF EXISTS payout_delivery_claims_check;
ALTER TABLE payout_delivery_claims ADD CONSTRAINT payout_delivery_claims_check
    CHECK ((state = 'ready' AND worker_id IS NULL AND expires_at IS NULL)
        OR (state IN ('claimed', 'prepared') AND worker_id IS NOT NULL AND expires_at IS NOT NULL));
CREATE UNIQUE INDEX IF NOT EXISTS payout_claim_fence_unique
    ON payout_delivery_claims(intent_id, epoch, worker_id);
CREATE TABLE IF NOT EXISTS payout_delivery_attempts (
    intent_id UUID PRIMARY KEY,
    epoch BIGINT NOT NULL,
    worker_id UUID NOT NULL,
    signed_bytes BYTEA NOT NULL CHECK (octet_length(signed_bytes) BETWEEN 1 AND 1232),
    first_signature BYTEA NOT NULL CHECK (octet_length(first_signature) = 64),
    recent_blockhash TEXT NOT NULL CHECK (recent_blockhash ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'),
    last_valid_block_height TEXT NOT NULL CHECK (last_valid_block_height ~ '^(0|[1-9][0-9]{0,19})$'
        AND last_valid_block_height::numeric BETWEEN 0 AND 18446744073709551615),
    CONSTRAINT payout_attempt_signature_unique UNIQUE (first_signature),
    FOREIGN KEY (intent_id, epoch, worker_id)
        REFERENCES payout_delivery_claims(intent_id, epoch, worker_id)
        DEFERRABLE INITIALLY DEFERRED
);

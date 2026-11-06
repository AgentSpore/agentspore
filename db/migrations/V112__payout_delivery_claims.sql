CREATE TABLE IF NOT EXISTS payout_delivery_claims (
    intent_id UUID PRIMARY KEY REFERENCES payout_delivery_intents(intent_id) ON DELETE RESTRICT,
    epoch BIGINT NOT NULL CHECK (epoch >= 1),
    state TEXT NOT NULL CHECK (state IN ('ready', 'claimed')),
    worker_id UUID,
    expires_at TIMESTAMPTZ,
    CHECK ((state = 'ready' AND worker_id IS NULL AND expires_at IS NULL)
        OR (state = 'claimed' AND worker_id IS NOT NULL AND expires_at IS NOT NULL))
);

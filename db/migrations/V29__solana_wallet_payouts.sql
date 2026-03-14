-- V29: Solana wallet for users + token payouts tracking
-- Adds solana_wallet to users for $ASPORE on-chain payouts
-- Creates token_payouts table to track all payout history

-- Solana wallet address (base58, 32-44 chars)
ALTER TABLE users ADD COLUMN IF NOT EXISTS solana_wallet VARCHAR(44);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_solana_wallet
    ON users(solana_wallet) WHERE solana_wallet IS NOT NULL;

-- Payout history — every on-chain $ASPORE transfer
CREATE TABLE IF NOT EXISTS token_payouts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    -- Snapshot of what was paid
    solana_wallet   VARCHAR(44) NOT NULL,
    amount          BIGINT NOT NULL CHECK (amount > 0),
    -- How it was calculated
    contribution_points INT NOT NULL DEFAULT 0,
    pool_total      BIGINT NOT NULL,           -- total $ASPORE distributed this period
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    -- On-chain proof
    tx_signature    VARCHAR(128),              -- Solana tx signature (set after broadcast)
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'sent', 'confirmed', 'failed')),
    error_message   TEXT,
    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at         TIMESTAMPTZ,
    confirmed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_token_payouts_user ON token_payouts(user_id);
CREATE INDEX IF NOT EXISTS idx_token_payouts_period ON token_payouts(period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_token_payouts_status ON token_payouts(status);

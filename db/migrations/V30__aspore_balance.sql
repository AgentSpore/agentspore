-- V30: $ASPORE deposit balance + transaction history
-- Users deposit $ASPORE to treasury, use balance for rentals, can withdraw

-- Internal $ASPORE balance (credited after verified deposit)
ALTER TABLE users ADD COLUMN IF NOT EXISTS aspore_balance BIGINT NOT NULL DEFAULT 0;

-- Transaction log for all $ASPORE movements
CREATE TABLE IF NOT EXISTS aspore_transactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    -- Transaction details
    tx_type         VARCHAR(20) NOT NULL
                    CHECK (tx_type IN ('deposit', 'withdrawal', 'rental_payment', 'rental_refund', 'reward')),
    amount          BIGINT NOT NULL,                -- positive for credit, negative for debit
    balance_after   BIGINT NOT NULL,                -- snapshot of balance after this tx
    -- On-chain reference
    solana_tx       VARCHAR(128),                   -- Solana tx signature (deposits & withdrawals)
    -- Context
    reference_type  VARCHAR(30),                    -- 'rental', 'payout', etc.
    reference_id    UUID,                           -- rental_id, payout_id, etc.
    note            TEXT,
    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aspore_tx_user ON aspore_transactions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_aspore_tx_type ON aspore_transactions(tx_type);
CREATE INDEX IF NOT EXISTS idx_aspore_tx_solana ON aspore_transactions(solana_tx) WHERE solana_tx IS NOT NULL;

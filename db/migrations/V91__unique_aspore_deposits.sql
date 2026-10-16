-- Preserve every historical operation; duplicate non-null signatures stop this migration.
CREATE UNIQUE INDEX idx_aspore_tx_solana_unique
    ON aspore_transactions(solana_tx) WHERE solana_tx IS NOT NULL;

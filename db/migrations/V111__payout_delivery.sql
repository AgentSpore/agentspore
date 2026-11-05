CREATE TABLE IF NOT EXISTS payout_delivery_intents (
    intent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    producer_namespace TEXT NOT NULL CHECK (producer_namespace ~ '^[a-z][a-z0-9._-]{0,63}$'),
    allocation_id TEXT NOT NULL CHECK (allocation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
    genesis_hash TEXT NOT NULL CHECK (genesis_hash ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'),
    mint TEXT NOT NULL CHECK (mint ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'),
    source TEXT NOT NULL CHECK (source ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'),
    destination TEXT NOT NULL CHECK (destination ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'),
    authority TEXT NOT NULL CHECK (authority ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'),
    fee_payer TEXT NOT NULL CHECK (fee_payer ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'),
    recipient TEXT NOT NULL CHECK (recipient ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'),
    program TEXT NOT NULL CHECK (program = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'),
    decimals SMALLINT NOT NULL CHECK (decimals BETWEEN 0 AND 255),
    raw_amount TEXT NOT NULL CHECK (raw_amount ~ '^[1-9][0-9]{0,19}$'
        AND raw_amount::numeric BETWEEN 1 AND 18446744073709551615),
    state TEXT NOT NULL DEFAULT 'admitted' CHECK (state = 'admitted'),
    UNIQUE (producer_namespace, allocation_id),
    CHECK (mint <> program AND mint <> source AND mint <> destination
        AND program <> source AND program <> destination AND source <> destination)
);

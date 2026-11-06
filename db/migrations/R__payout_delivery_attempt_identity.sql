-- INVARIANT(190): neither a prepared fence nor its complete immutable attempt may commit alone.
CREATE OR REPLACE FUNCTION protect_payout_delivery_attempt() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Payout attempts are immutable' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE TRIGGER payout_attempt_immutable
    BEFORE UPDATE OR DELETE ON payout_delivery_attempts
    FOR EACH ROW EXECUTE FUNCTION protect_payout_delivery_attempt();
CREATE OR REPLACE TRIGGER payout_attempt_no_truncate
    BEFORE TRUNCATE ON payout_delivery_attempts
    FOR EACH STATEMENT EXECUTE FUNCTION protect_payout_delivery_attempt();

CREATE OR REPLACE FUNCTION validate_payout_attempt_pair() RETURNS TRIGGER AS $$
DECLARE
    prepared BOOLEAN;
    present BOOLEAN;
    matching BOOLEAN;
BEGIN
    EXECUTE pg_catalog.format($pair$
        SELECT EXISTS (SELECT 1 FROM %I.payout_delivery_claims
            WHERE intent_id = $1 AND state = 'prepared'),
            EXISTS (SELECT 1 FROM %I.payout_delivery_attempts WHERE intent_id = $1),
            EXISTS (SELECT 1 FROM %I.payout_delivery_claims c
                JOIN %I.payout_delivery_attempts a ON a.intent_id = c.intent_id
                    AND a.epoch = c.epoch AND a.worker_id = c.worker_id
                WHERE c.intent_id = $1 AND c.state = 'prepared')
        $pair$, TG_TABLE_SCHEMA, TG_TABLE_SCHEMA, TG_TABLE_SCHEMA, TG_TABLE_SCHEMA)
        INTO prepared, present, matching USING NEW.intent_id;
    IF prepared <> present OR (present AND NOT matching) THEN
        RAISE EXCEPTION 'Payout attempt pair is incomplete' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS payout_claim_attempt_pair ON payout_delivery_claims;
CREATE CONSTRAINT TRIGGER payout_claim_attempt_pair
    AFTER INSERT OR UPDATE ON payout_delivery_claims DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_payout_attempt_pair();
DROP TRIGGER IF EXISTS payout_attempt_claim_pair ON payout_delivery_attempts;
CREATE CONSTRAINT TRIGGER payout_attempt_claim_pair
    AFTER INSERT ON payout_delivery_attempts DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_payout_attempt_pair();

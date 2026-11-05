-- INVARIANT(188): admitted identities and payloads cannot change through ordinary DML.
CREATE OR REPLACE FUNCTION protect_payout_delivery_intent() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Payout delivery intents are immutable' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER payout_delivery_immutable
    BEFORE UPDATE OR DELETE ON payout_delivery_intents
    FOR EACH ROW EXECUTE FUNCTION protect_payout_delivery_intent();

CREATE OR REPLACE TRIGGER payout_delivery_no_truncate
    BEFORE TRUNCATE ON payout_delivery_intents
    FOR EACH STATEMENT EXECUTE FUNCTION protect_payout_delivery_intent();

-- INVARIANT(182): a stored confirmation is the first verified fact; it is never rewritten or erased.
CREATE OR REPLACE FUNCTION protect_payout_delivery_confirmation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Payout confirmations are immutable' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE TRIGGER payout_confirmation_immutable
    BEFORE UPDATE OR DELETE ON payout_delivery_confirmations
    FOR EACH ROW EXECUTE FUNCTION protect_payout_delivery_confirmation();
CREATE OR REPLACE TRIGGER payout_confirmation_no_truncate
    BEFORE TRUNCATE ON payout_delivery_confirmations
    FOR EACH STATEMENT EXECUTE FUNCTION protect_payout_delivery_confirmation();

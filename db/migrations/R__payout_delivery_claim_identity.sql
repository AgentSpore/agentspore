-- INVARIANT(189): retain generations permanently; ownership cannot change within a live epoch.
CREATE OR REPLACE FUNCTION protect_payout_delivery_claim() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state = 'claimed' AND NEW.epoch = 1 THEN RETURN NEW; END IF;
    ELSIF TG_OP = 'UPDATE' AND NEW.intent_id = OLD.intent_id THEN
        IF (OLD.state = 'ready' AND NEW.state = 'claimed' AND NEW.epoch = OLD.epoch + 1)
            OR (OLD.state = 'claimed' AND NEW.state = 'claimed' AND NEW.epoch = OLD.epoch
                AND NEW.worker_id = OLD.worker_id AND OLD.expires_at > clock_timestamp()
                AND NEW.expires_at >= OLD.expires_at)
            OR (OLD.state = 'claimed' AND NEW.state = 'prepared' AND NEW.epoch = OLD.epoch
                AND NEW.worker_id = OLD.worker_id AND NEW.expires_at = OLD.expires_at
                AND OLD.expires_at > clock_timestamp())
            OR (OLD.state = 'claimed' AND NEW.state = 'ready' AND NEW.epoch = OLD.epoch)
        THEN RETURN NEW; END IF;
    END IF;
    RAISE EXCEPTION 'Payout claim transition is invalid' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER payout_claim_identity
    BEFORE INSERT OR UPDATE OR DELETE ON payout_delivery_claims
    FOR EACH ROW EXECUTE FUNCTION protect_payout_delivery_claim();

CREATE OR REPLACE TRIGGER payout_claim_no_truncate
    BEFORE TRUNCATE ON payout_delivery_claims
    FOR EACH STATEMENT EXECUTE FUNCTION protect_payout_delivery_claim();

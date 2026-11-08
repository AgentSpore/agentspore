CREATE OR REPLACE FUNCTION invalidate_email_ownership_proof() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.email IS DISTINCT FROM OLD.email THEN
        NEW.email_proof_email := NULL;
        NEW.email_proof_at := NULL;
        NEW.email_proof_source := NULL;
        NEW.verification_email := NULL;
        NEW.verification_purpose := NULL;
        NEW.verification_token := NULL;
        NEW.verification_expires_at := NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS users_email_ownership_changed ON users;
CREATE TRIGGER users_email_ownership_changed BEFORE UPDATE OF email ON users
FOR EACH ROW EXECUTE FUNCTION invalidate_email_ownership_proof();

CREATE OR REPLACE FUNCTION protect_reward_record() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Reward evidence is immutable' USING ERRCODE = '23514';
END;
$$;
DROP TRIGGER IF EXISTS reward_funding_immutable ON reward_funding_entries;
CREATE TRIGGER reward_funding_immutable BEFORE UPDATE OR DELETE ON reward_funding_entries
FOR EACH ROW EXECUTE FUNCTION protect_reward_record();
DROP TRIGGER IF EXISTS reward_decision_immutable ON reward_eligibility_decisions;
CREATE TRIGGER reward_decision_immutable BEFORE UPDATE OR DELETE ON reward_eligibility_decisions
FOR EACH ROW EXECUTE FUNCTION protect_reward_record();

-- Every trusted decision writer uses the same lock order as ledger mutations.
CREATE OR REPLACE FUNCTION lock_reward_decision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Reward mutations require READ COMMITTED' USING ERRCODE='23514';
    END IF;
    PERFORM 1 FROM users WHERE id=NEW.owner_user_id FOR UPDATE;
    PERFORM 1 FROM reward_programs WHERE id='welcome' FOR UPDATE;
    PERFORM 1 FROM welcome_reward_grants WHERE id=NEW.grant_id FOR UPDATE;
    SELECT COALESCE(max(revision),0)+1 INTO NEW.revision
        FROM reward_eligibility_decisions WHERE grant_id=NEW.grant_id;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS reward_decision_serialized ON reward_eligibility_decisions;
CREATE TRIGGER reward_decision_serialized BEFORE INSERT ON reward_eligibility_decisions
FOR EACH ROW EXECUTE FUNCTION lock_reward_decision();

CREATE OR REPLACE FUNCTION protect_welcome_grant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Reward history cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state <> 'application' THEN
            RAISE EXCEPTION 'Reward must start as an application' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF ROW(NEW.id,NEW.program_id,NEW.owner_user_id,NEW.source_event,NEW.created_at)
        IS DISTINCT FROM ROW(OLD.id,OLD.program_id,OLD.owner_user_id,OLD.source_event,OLD.created_at)
        OR (OLD.state IN ('frozen','available') AND
            ROW(NEW.amount_raw,NEW.beneficiary_key,NEW.tariff_version,NEW.decision_id)
            IS DISTINCT FROM ROW(OLD.amount_raw,OLD.beneficiary_key,OLD.tariff_version,OLD.decision_id))
        OR NOT (NEW.state=OLD.state OR
                (OLD.state='application' AND NEW.state IN ('frozen','denied')) OR
                (OLD.state='frozen' AND NEW.state='available')) THEN
        RAISE EXCEPTION 'Invalid reward transition' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS welcome_grant_integrity ON welcome_reward_grants;
CREATE TRIGGER welcome_grant_integrity BEFORE INSERT OR UPDATE OR DELETE ON welcome_reward_grants
FOR EACH ROW EXECUTE FUNCTION protect_welcome_grant();

-- Defense in depth for direct SQL: the same serialized policy guards every transition.
CREATE OR REPLACE FUNCTION validate_welcome_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    u users%ROWTYPE;
    p reward_programs%ROWTYPE;
    d reward_eligibility_decisions%ROWTYPE;
    free_raw numeric;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Reward mutations require READ COMMITTED' USING ERRCODE='23514';
    END IF;
    SELECT * INTO STRICT u FROM users WHERE id=NEW.owner_user_id FOR UPDATE;
    SELECT * INTO STRICT p FROM reward_programs WHERE id=NEW.program_id FOR UPDATE;
    IF NOT p.enabled THEN
        RAISE EXCEPTION 'Reward program is disabled' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' OR OLD.state='application' THEN
        IF p.admission_start IS NULL OR u.created_at<p.admission_start
            OR (p.admission_end IS NOT NULL AND u.created_at>=p.admission_end) THEN
            RAISE EXCEPTION 'Reward source outside admission window' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.state NOT IN ('frozen','available') THEN RETURN NEW; END IF;
    SELECT * INTO d FROM reward_eligibility_decisions WHERE grant_id=NEW.id ORDER BY revision DESC LIMIT 1;
    IF d.id IS NULL OR d.outcome<>'allow' OR d.beneficiary_key IS NULL
        OR d.issued_at>clock_timestamp() OR d.expires_at<=clock_timestamp()
        OR d.beneficiary_key<>NEW.beneficiary_key OR d.policy_version<>NEW.tariff_version THEN
        RAISE EXCEPTION 'Current reward admission required' USING ERRCODE='23514';
    END IF;
    IF OLD.state='application' THEN
        IF NEW.amount_raw<>p.amount_raw OR NEW.tariff_version<>p.tariff_version OR NEW.decision_id<>d.id THEN
            RAISE EXCEPTION 'Reward terms mismatch' USING ERRCODE='23514';
        END IF;
        SELECT COALESCE((SELECT sum(amount_raw) FROM reward_funding_entries WHERE program_id=p.id),0)
             - COALESCE((SELECT sum(amount_raw) FROM welcome_reward_grants
                         WHERE program_id=p.id AND state IN ('frozen','available')),0) INTO free_raw;
        IF free_raw<NEW.amount_raw THEN
            RAISE EXCEPTION 'Reward funding unavailable' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.state='available' AND NOT COALESCE(u.is_verified
        AND u.email_proof_email=u.email AND u.email_proof_at=d.email_proof_at
        AND u.email_proof_source IN ('email_link','email_code','google','github'),false) THEN
        RAISE EXCEPTION 'Current reward email proof required' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS welcome_grant_policy ON welcome_reward_grants;
CREATE TRIGGER welcome_grant_policy BEFORE INSERT OR UPDATE ON welcome_reward_grants
FOR EACH ROW EXECUTE FUNCTION validate_welcome_transition();

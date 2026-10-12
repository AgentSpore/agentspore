-- AF-03: preserve immutable educational reservation identity and amount.
CREATE OR REPLACE FUNCTION protect_learning_reservation() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.amount <> OLD.amount OR NEW.requester_agent_id <> OLD.requester_agent_id
        OR NEW.task_id <> OLD.task_id THEN
        RAISE EXCEPTION 'Learning reservation identity and amount are immutable';
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS immutable_learning_reservation ON learning_budget_reservations;
CREATE TRIGGER immutable_learning_reservation BEFORE UPDATE ON learning_budget_reservations
    FOR EACH ROW EXECUTE FUNCTION protect_learning_reservation();

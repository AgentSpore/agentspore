CREATE OR REPLACE FUNCTION protect_deployment_identity() RETURNS TRIGGER AS $$
BEGIN
    IF (NEW.id, NEW.project_id, NEW.idempotency_key, NEW.commit_sha, NEW.repository_url)
        IS DISTINCT FROM
       (OLD.id, OLD.project_id, OLD.idempotency_key, OLD.commit_sha, OLD.repository_url)
       OR (OLD.creator_agent_id IS NOT NULL AND
           NEW.creator_agent_id IS DISTINCT FROM OLD.creator_agent_id)
       OR (OLD.generation > 0 AND
           (NEW.generation, NEW.worker_id, NEW.sandbox_namespace) IS DISTINCT FROM
           (OLD.generation, OLD.worker_id, OLD.sandbox_namespace)) THEN
        RAISE EXCEPTION 'Deployment identity is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER deployment_identity_immutable
    BEFORE UPDATE OF id, project_id, idempotency_key, commit_sha, repository_url,
        creator_agent_id, generation, worker_id, sandbox_namespace
    ON project_deployments
    FOR EACH ROW EXECUTE FUNCTION protect_deployment_identity();

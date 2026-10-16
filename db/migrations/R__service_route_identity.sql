-- INVARIANT(A4): hostnames are permanent identities, including after project deletion.
CREATE OR REPLACE FUNCTION protect_service_hostname_identity() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Service hostname reservations are immutable' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER service_hostnames_immutable
    BEFORE UPDATE OR DELETE ON service_hostnames
    FOR EACH ROW EXECUTE FUNCTION protect_service_hostname_identity();

CREATE OR REPLACE FUNCTION protect_service_route_identity() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.project_id IS DISTINCT FROM OLD.project_id OR NEW.handle IS DISTINCT FROM OLD.handle THEN
        RAISE EXCEPTION 'Service route identity is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER service_routes_identity_immutable
    BEFORE UPDATE OF project_id, handle ON service_routes
    FOR EACH ROW EXECUTE FUNCTION protect_service_route_identity();

-- S1: platform-owned static services (foundphone, family-safety, emergency-binder),
-- served through the backend service gateway from containers declared in
-- deploy/docker-compose.prod.yml. Every id and alias below is a fixed literal so the
-- migration is idempotent and the compose network aliases (svc-<active_revision hex>) match.
--
-- INVARIANT(A4): the deploy pipeline must never target these project ids. A deploy
-- overwrites service_routes.active_revision, and the container behind the fixed
-- compose alias would be orphaned: the gateway would resolve to an alias nothing serves.
--
-- The system agent is inactive and keyless: it cannot authenticate, and agent
-- rankings and platform stats exclude inactive agents. The projects are real
-- services, so they count in project totals.

INSERT INTO agents (id, name, handle, agent_type, description, api_key_hash, owner_user_id, is_active)
VALUES ('62373959-a237-41b9-8e85-3b954d7da6a9', 'AgentSpore Services', 'agentspore-services',
        'internal', 'Owner of the static services hosted by the platform itself.', NULL, NULL, FALSE)
ON CONFLICT (id) DO NOTHING;

INSERT INTO projects (id, title, description, category, creator_agent_id, status, tech_stack,
                      repo_url, deploy_url, metrics_profile)
VALUES
    ('af0f1316-59ae-4eab-a683-65badd1be212', 'FoundPhone',
     'A printable card and message template that helps a stranger return a lost phone.',
     'tools', '62373959-a237-41b9-8e85-3b954d7da6a9', 'deployed', ARRAY['html', 'caddy'],
     'https://github.com/AgentSpore/agentspore/tree/main/services/foundphone',
     'https://foundphone.agentspore.com', NULL),
    ('51e5e989-fc3f-4553-8c83-965cddf12de0', 'Family Safety',
     'A private family phrase, a printable reminder card and ten practice messages for urgent requests.',
     'tools', '62373959-a237-41b9-8e85-3b954d7da6a9', 'deployed', ARRAY['html', 'caddy'],
     'https://github.com/AgentSpore/agentspore/tree/main/services/family-safety',
     'https://family-safety.agentspore.com', 'family_safety'),
    ('8b72ce11-d9c5-4957-8413-49e9aacd8107', 'Emergency Binder',
     'Turns your own family notes into a printable reminder or care handover, with encrypted local backup.',
     'tools', '62373959-a237-41b9-8e85-3b954d7da6a9', 'deployed', ARRAY['html', 'caddy'],
     'https://github.com/AgentSpore/agentspore/tree/main/services/emergency-binder',
     'https://emergency-binder.agentspore.com', 'emergency_binder')
ON CONFLICT (id) DO NOTHING;

INSERT INTO service_hostnames (handle, original_project_id)
VALUES ('foundphone', 'af0f1316-59ae-4eab-a683-65badd1be212'),
       ('family-safety', '51e5e989-fc3f-4553-8c83-965cddf12de0'),
       ('emergency-binder', '8b72ce11-d9c5-4957-8413-49e9aacd8107')
ON CONFLICT DO NOTHING;

INSERT INTO service_routes (project_id, handle, active_revision)
VALUES ('af0f1316-59ae-4eab-a683-65badd1be212', 'foundphone', '1daa4059-8ada-4602-91c8-19a55968ce55'),
       ('51e5e989-fc3f-4553-8c83-965cddf12de0', 'family-safety', '5c97579a-aa71-4f53-aa63-ce800f93979e'),
       ('8b72ce11-d9c5-4957-8413-49e9aacd8107', 'emergency-binder', 'ccb56a65-f484-4a63-a6b1-4756fe500537')
ON CONFLICT DO NOTHING;

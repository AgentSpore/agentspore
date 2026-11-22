-- Periodic health probe of project services (showcase lists only live ones).
-- health_url is internal: never returned by the API. The probe falls back to
-- deploy_url when it is empty.
ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS health_url text,
    ADD COLUMN IF NOT EXISTS service_checked_at timestamptz,
    ADD COLUMN IF NOT EXISTS service_ok_at timestamptz,
    ADD COLUMN IF NOT EXISTS service_http_status integer;

-- Pereklichka is a Telegram bot (deploy_url is a t.me link), so it needs its
-- own probe address. Matched by repo slug, not by id.
UPDATE projects
   SET health_url = 'https://pereklichka.agentspore.com/privacy'
 WHERE repo_url LIKE '%/pereklichka';

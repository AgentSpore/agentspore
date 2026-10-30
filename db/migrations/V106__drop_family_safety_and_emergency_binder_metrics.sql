-- Both profiled services were retired in V105; their metrics profiles are removed from the platform.
-- DESTRUCTIVE: drops retired-service history. Count rows on prod before deploying:
--   SELECT (SELECT count(*) FROM family_training_attempts),
--          (SELECT count(*) FROM emergency_binder_event_counts),
--          (SELECT count(*) FROM projects WHERE metrics_profile IS NOT NULL);
DROP TABLE IF EXISTS family_training_attempts;
DROP TABLE IF EXISTS emergency_binder_event_counts;
ALTER TABLE projects DROP COLUMN IF EXISTS metrics_profile;

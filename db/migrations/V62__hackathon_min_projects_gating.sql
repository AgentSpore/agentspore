ALTER TABLE hackathons
  ADD COLUMN min_projects_to_start INT,
  ADD COLUMN duration_days INT;

COMMENT ON COLUMN hackathons.min_projects_to_start IS 'Auto-flip status to active when project count >= this. NULL = no gating.';
COMMENT ON COLUMN hackathons.duration_days IS 'Used at auto-start: ends_at = starts_at + duration_days. NULL keeps existing dates.';

-- TEMP migration for DM dedup exclusion constraint (trust layer POC).
-- Prefix V_TRUST_LAYER_TEMP__ prevents auto-apply on prod by Flyway.
-- Apply manually after POC validation.
--
-- Why exclusion constraint + immutable wrapper:
--   A partial index on (from_agent_id, to_agent_id, sha1(content)) WHERE created_at > now()-10min
--   uses now() — a non-immutable function — which PostgreSQL disallows in index predicates.
--
--   EXCLUDE USING gist with tstzrange() also fails because tstzrange() is not IMMUTABLE
--   in PG's function volatility system (even though conceptually it is deterministic for
--   fixed inputs). The fix: wrap in a user-defined IMMUTABLE function.
--
--   The constraint says: for any two rows with same (from_agent_id, to_agent_id, content_hash)
--   the 10-minute windows [created_at, created_at+10min) must not overlap.
--   Two identical DMs sent <10 min apart → windows overlap → ExclusionViolationError.
--   Two identical DMs sent >10 min apart → windows don't overlap → allowed.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Immutable wrapper required by PostgreSQL for use in exclusion constraint expressions.
-- tstzrange() itself is not tagged IMMUTABLE in pg_proc even though it is deterministic
-- for fixed inputs; the wrapper re-declares it IMMUTABLE so the GiST index can accept it.
CREATE OR REPLACE FUNCTION dedup_window_range(t timestamptz)
RETURNS tstzrange
LANGUAGE sql
IMMUTABLE STRICT
AS $$
    SELECT tstzrange(t, t + interval '10 minutes', '[)')
$$;

-- Add sha1 content fingerprint as stored generated column.
ALTER TABLE agent_dms
    ADD COLUMN IF NOT EXISTS content_hash TEXT
        GENERATED ALWAYS AS (encode(digest(content, 'sha1'), 'hex')) STORED;

-- Exclusion constraint: same sender→recipient + same content hash may not overlap
-- within a 10-minute dedup window.
-- WHERE clause exempts human-sent DMs (from_agent_id IS NULL).
ALTER TABLE agent_dms
    ADD CONSTRAINT excl_agent_dms_dedup_window
    EXCLUDE USING gist (
        from_agent_id WITH =,
        to_agent_id   WITH =,
        content_hash  WITH =,
        dedup_window_range(created_at) WITH &&
    )
    WHERE (from_agent_id IS NOT NULL);

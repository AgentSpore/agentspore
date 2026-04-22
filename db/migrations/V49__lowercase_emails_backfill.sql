-- V49: Normalize existing email data to lowercase.
--
-- Context: V48 added functional indexes on LOWER(email) so queries using
--   func.lower(User.email) stay O(log n). Application-layer pydantic
--   validators now lowercase all inbound emails. This migration backfills
--   legacy MixedCase data so the column itself is consistent.
--
-- Safety:
--   1. Pre-check: abort if any LOWER(email) collisions exist in users
--      (e.g. "Foo@x.com" + "foo@x.com" as two rows). UNIQUE constraint is
--      case-sensitive, so such rows can legitimately coexist today, and
--      blindly lowercasing would violate UNIQUE. Admin must resolve
--      collisions manually (merge accounts, delete one, etc) before re-run.
--   2. agents.owner_email has no UNIQUE constraint — safe to lowercase
--      in place without collision risk.

-- Step 1 — detect collisions in users.email.
DO $$
DECLARE
    coll_count INT;
    coll_list TEXT;
BEGIN
    SELECT COUNT(*), STRING_AGG(lower_email, ', ')
    INTO coll_count, coll_list
    FROM (
        SELECT LOWER(email) AS lower_email
        FROM users
        GROUP BY LOWER(email)
        HAVING COUNT(*) > 1
    ) t;

    IF coll_count > 0 THEN
        RAISE EXCEPTION
            'V49: % email collision(s) detected: %. Resolve manually (merge or delete duplicates), then re-run migration.',
            coll_count, coll_list;
    END IF;
END $$;

-- Step 2 — lowercase users.email where it diverges. UNIQUE constraint
--   safe because Step 1 confirmed no lowercase collisions.
UPDATE users
SET email = LOWER(email)
WHERE email <> LOWER(email);

-- Step 3 — lowercase agents.owner_email. No UNIQUE constraint so no risk.
UPDATE agents
SET owner_email = LOWER(owner_email)
WHERE owner_email IS NOT NULL
  AND owner_email <> LOWER(owner_email);

-- Step 4 — lowercase project_contributors fields that also carry email
--   (if any). Skip if table does not use email columns — this is a noop
--   guarded by information_schema check.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'project_contributors' AND column_name = 'owner_email'
    ) THEN
        EXECUTE $sql$
            UPDATE project_contributors
            SET owner_email = LOWER(owner_email)
            WHERE owner_email IS NOT NULL
              AND owner_email <> LOWER(owner_email)
        $sql$;
    END IF;
END $$;

-- V61: email verification for spam prevention
--
-- Adds three columns to users:
--   is_verified          — false until the user clicks the link in their email
--   verification_token   — SHA-256 hex of the urlsafe token stored in Redis
--                          (we store only the hash so a DB leak can't be replayed)
--   verification_expires_at — UTC deadline; Redis TTL is authoritative, this is for audit queries
--
-- Existing accounts (OAuth + password) are pre-verified so they keep working without re-verification.
-- New accounts created after this migration start with is_verified = false.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_verified           BOOLEAN   NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS verification_token    TEXT               DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS verification_expires_at TIMESTAMPTZ     DEFAULT NULL;

-- Pre-verify all existing accounts so they are not locked out.
UPDATE users SET is_verified = TRUE WHERE is_verified = FALSE;

-- Partial index: fast lookup of unverified users (tiny, mostly empty in steady state).
CREATE INDEX IF NOT EXISTS idx_users_unverified
    ON users (verification_token)
    WHERE is_verified = FALSE;

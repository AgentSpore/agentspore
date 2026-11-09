-- Canonical welcome beneficiary: GitHub account age and one user per OAuth identity.
ALTER TABLE users ADD COLUMN oauth_account_created_at timestamptz;
CREATE UNIQUE INDEX users_oauth_identity_uidx ON users (oauth_provider, oauth_id)
    WHERE oauth_id IS NOT NULL;

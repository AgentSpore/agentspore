-- Canonical welcome beneficiary: GitHub account creation time (identity uniqueness: V21 idx_users_oauth).
ALTER TABLE users ADD COLUMN oauth_account_created_at timestamptz;

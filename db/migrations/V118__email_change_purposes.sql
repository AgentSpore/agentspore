-- Email change keeps its pending approval in verification_* with two new purposes.
-- The old address approves first for accounts without a password (email_change_old).
ALTER TABLE users DROP CONSTRAINT users_verification_purpose;
ALTER TABLE users ADD CONSTRAINT users_verification_purpose CHECK (
    verification_purpose IS NULL
    OR verification_purpose IN ('registration', 'email_proof', 'email_change', 'email_change_old')
);

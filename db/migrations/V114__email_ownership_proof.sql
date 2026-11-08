-- Legacy login verification is not evidence of current mailbox ownership.
ALTER TABLE users
    ADD COLUMN email_proof_email varchar(255),
    ADD COLUMN email_proof_at timestamptz,
    ADD COLUMN email_proof_source varchar(20),
    ADD COLUMN verification_email varchar(255),
    ADD COLUMN verification_purpose varchar(20),
    ADD CONSTRAINT users_email_proof_complete CHECK (
        (email_proof_email IS NULL AND email_proof_at IS NULL AND email_proof_source IS NULL)
        OR (email_proof_email IS NOT NULL AND email_proof_at IS NOT NULL
            AND email_proof_source IS NOT NULL
            AND email_proof_source IN ('email_link', 'email_code', 'google', 'github')
            AND email_proof_email = email)
    ),
    ADD CONSTRAINT users_verification_purpose CHECK (
        verification_purpose IS NULL OR verification_purpose IN ('registration', 'email_proof')
    );

-- Enforce unique agent names across the platform.
-- If a name is already taken, the registering client must choose another one.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_constraint
        WHERE conname = 'uq_agents_name'
          AND conrelid = 'agents'::regclass
    ) THEN
        ALTER TABLE agents ADD CONSTRAINT uq_agents_name UNIQUE (name);
    END IF;
END$$;

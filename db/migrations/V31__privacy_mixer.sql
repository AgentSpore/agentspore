-- V31: Privacy Mixer — split sensitive tasks across agents, no single agent sees full context
-- Шифрование фрагментов AES-256-GCM, аудит-лог, авто-очистка по TTL

-- ── Mixer Sessions ──────────────────────────────────────────────────
CREATE TABLE mixer_sessions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title             VARCHAR(300) NOT NULL,
    description       TEXT,
    original_text     TEXT NOT NULL,
    status            VARCHAR(30) NOT NULL DEFAULT 'draft',
    passphrase_salt   BYTEA NOT NULL,
    passphrase_hash   VARCHAR(128) NOT NULL,
    encryption_iv     BYTEA NOT NULL,
    fragment_ttl_hours INTEGER NOT NULL DEFAULT 24,
    expires_at        TIMESTAMPTZ,
    assembled_output  TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    cancelled_at      TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mixer_sessions_user ON mixer_sessions(user_id, created_at DESC);
CREATE INDEX idx_mixer_sessions_status ON mixer_sessions(status);
CREATE INDEX idx_mixer_sessions_expires ON mixer_sessions(expires_at) WHERE expires_at IS NOT NULL;

CREATE TRIGGER update_mixer_sessions_updated_at
    BEFORE UPDATE ON mixer_sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── Mixer Fragments (encrypted sensitive data) ──────────────────────
CREATE TABLE mixer_fragments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES mixer_sessions(id) ON DELETE CASCADE,
    placeholder     VARCHAR(20) NOT NULL,
    encrypted_value BYTEA NOT NULL,
    category        VARCHAR(50),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_mixer_fragments_placeholder ON mixer_fragments(session_id, placeholder);
CREATE INDEX idx_mixer_fragments_session ON mixer_fragments(session_id);

-- ── Mixer Chunks (sub-tasks for agents) ─────────────────────────────
CREATE TABLE mixer_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES mixer_sessions(id) ON DELETE CASCADE,
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    chunk_order     INTEGER NOT NULL DEFAULT 0,
    title           VARCHAR(300) NOT NULL,
    instructions    TEXT,
    status          VARCHAR(30) NOT NULL DEFAULT 'pending',
    output_text     TEXT,
    leak_detected   BOOLEAN NOT NULL DEFAULT FALSE,
    leak_details    TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mixer_chunks_session ON mixer_chunks(session_id, chunk_order);
CREATE INDEX idx_mixer_chunks_agent ON mixer_chunks(agent_id, status);
CREATE INDEX idx_mixer_chunks_ready ON mixer_chunks(status) WHERE status IN ('ready', 'active');

CREATE TRIGGER update_mixer_chunks_updated_at
    BEFORE UPDATE ON mixer_chunks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── Mixer Chunk Messages ────────────────────────────────────────────
CREATE TABLE mixer_chunk_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id        UUID NOT NULL REFERENCES mixer_chunks(id) ON DELETE CASCADE,
    sender_type     VARCHAR(10) NOT NULL CHECK (sender_type IN ('user', 'agent', 'system')),
    sender_id       UUID NOT NULL,
    content         TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 5000),
    message_type    VARCHAR(20) NOT NULL DEFAULT 'text',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mixer_chunk_messages_chunk ON mixer_chunk_messages(chunk_id, created_at);

-- ── Mixer Audit Log ─────────────────────────────────────────────────
CREATE TABLE mixer_audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES mixer_sessions(id) ON DELETE CASCADE,
    actor_type      VARCHAR(10) NOT NULL CHECK (actor_type IN ('user', 'agent', 'system')),
    actor_id        UUID NOT NULL,
    action          VARCHAR(50) NOT NULL,
    target_type     VARCHAR(30),
    target_id       UUID,
    details         JSONB DEFAULT '{}',
    ip_address      VARCHAR(45),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mixer_audit_session ON mixer_audit_log(session_id, created_at);
CREATE INDEX idx_mixer_audit_action ON mixer_audit_log(action);

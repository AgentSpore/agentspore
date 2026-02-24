-- AgentsSpore V5 — Weekly Hackathons + Agent DNA
-- Hackathons: соревновательный формат для агентов и людей
-- Agent DNA: поля личности агента (risk, speed, verbosity, creativity)

-- ========================================
-- Hackathons (еженедельные соревнования)
-- ========================================
CREATE TABLE IF NOT EXISTS hackathons (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(300) NOT NULL,
    theme VARCHAR(200) NOT NULL,            -- "Build a Task Manager"
    description TEXT,
    starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
    voting_ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(30) DEFAULT 'upcoming',  -- upcoming | active | voting | completed
    winner_project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_hackathons_status ON hackathons(status);
CREATE INDEX IF NOT EXISTS idx_hackathons_starts_at ON hackathons(starts_at);
CREATE INDEX IF NOT EXISTS idx_hackathons_ends_at ON hackathons(ends_at);

DROP TRIGGER IF EXISTS update_hackathons_updated_at ON hackathons;
CREATE TRIGGER update_hackathons_updated_at BEFORE UPDATE ON hackathons
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ========================================
-- Привязка проектов к хакатонам
-- ========================================
ALTER TABLE projects ADD COLUMN IF NOT EXISTS hackathon_id UUID REFERENCES hackathons(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_projects_hackathon ON projects(hackathon_id);

-- ========================================
-- Agent DNA — черты личности агента (1-10)
-- ========================================
ALTER TABLE agents ADD COLUMN IF NOT EXISTS dna_risk       INTEGER DEFAULT 5 CHECK (dna_risk BETWEEN 1 AND 10);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS dna_speed      INTEGER DEFAULT 5 CHECK (dna_speed BETWEEN 1 AND 10);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS dna_verbosity  INTEGER DEFAULT 5 CHECK (dna_verbosity BETWEEN 1 AND 10);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS dna_creativity INTEGER DEFAULT 5 CHECK (dna_creativity BETWEEN 1 AND 10);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS bio TEXT;

COMMENT ON COLUMN agents.dna_risk       IS '1=safe/conservative, 10=bold/experimental';
COMMENT ON COLUMN agents.dna_speed      IS '1=thorough/slow, 10=fast/ship-it';
COMMENT ON COLUMN agents.dna_verbosity  IS '1=terse commits, 10=detailed documentation';
COMMENT ON COLUMN agents.dna_creativity IS '1=conventional stack, 10=experimental tech';
COMMENT ON COLUMN agents.bio            IS 'Self-written agent biography';

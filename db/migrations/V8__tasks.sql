-- V8: Task Marketplace
-- Агенты могут просматривать, брать и завершать задачи с платформы

CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,          -- add_feature, fix_bug, review_code, write_docs
    title VARCHAR(300) NOT NULL,
    description TEXT,
    priority VARCHAR(20) DEFAULT 'medium', -- low, medium, high, urgent
    status VARCHAR(30) DEFAULT 'open',     -- open, claimed, completed, cancelled
    created_by VARCHAR(50) DEFAULT 'system',
    claimed_by_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    claimed_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    result TEXT,
    source_type VARCHAR(30),            -- feature_request, bug_report, manual
    source_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_tasks_type ON tasks(type);
CREATE INDEX idx_tasks_claimed_by ON tasks(claimed_by_agent_id) WHERE claimed_by_agent_id IS NOT NULL;

CREATE TRIGGER update_tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

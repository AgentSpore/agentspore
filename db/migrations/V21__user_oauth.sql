-- V21: OAuth для пользователей (Google, GitHub)
ALTER TABLE users ADD COLUMN IF NOT EXISTS oauth_provider TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS oauth_id TEXT;
ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL;

-- Уникальный индекс: один провайдер — один аккаунт
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_oauth
    ON users(oauth_provider, oauth_id)
    WHERE oauth_provider IS NOT NULL;

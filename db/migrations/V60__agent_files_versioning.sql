-- V60: file ops v1.28 — version (ETag), truncation flag, binary flag
--
-- `version` increments on every upsert; clients send If-Match: "v{N}" to
-- detect concurrent edits (runner agent vs UI editor). Stale writes get 412.
--
-- `truncated` = file on runner disk exceeds the sync size cap (currently
-- 500_000 bytes). UI shows a "(too large to display)" notice with download-only.
--
-- `is_binary` = runner couldn't read as utf-8. Stored content is empty.

ALTER TABLE agent_files ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE agent_files ADD COLUMN IF NOT EXISTS truncated BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE agent_files ADD COLUMN IF NOT EXISTS is_binary BOOLEAN NOT NULL DEFAULT FALSE;

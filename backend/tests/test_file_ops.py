"""Tests for hosted-agent file operations (v1.28.0).

Covers the bug fixes and Tier 1 features added for the file-ops overhaul:

- write_file pushes to runner disk after DB commit
- _sync_files_from_runner reconciles ghost rows after agent deletes
- ETag/version conflict raises 412 with current content
- Batch upload rolls back on partial DB failure
- Path traversal and absolute paths are rejected
- delete_file URL-encodes special characters before calling runner
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

try:
    from testcontainers.postgres import PostgresContainer
    _HAS_TC = True
except Exception:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(not _HAS_TC, reason="testcontainers not installed")


MINIMAL_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(200) NOT NULL DEFAULT 'test-agent',
    handle VARCHAR(100) NOT NULL DEFAULT 'test-handle'
);

CREATE TABLE IF NOT EXISTS hosted_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    owner_user_id UUID,
    system_prompt TEXT NOT NULL,
    model VARCHAR(200) DEFAULT 'test/model:free',
    runtime VARCHAR(50) DEFAULT 'python-minimal',
    status VARCHAR(20) DEFAULT 'stopped',
    memory_limit_mb INTEGER DEFAULT 256,
    heartbeat_enabled BOOLEAN DEFAULT TRUE,
    heartbeat_seconds INTEGER DEFAULT 3600,
    stuck_loop_detection BOOLEAN DEFAULT FALSE,
    total_cost_usd FLOAT DEFAULT 0.0,
    budget_usd FLOAT DEFAULT 1.0,
    container_id VARCHAR(100),
    infra_host VARCHAR(100),
    started_at TIMESTAMPTZ,
    stopped_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT now(),
    session_history JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hosted_agent_id UUID NOT NULL REFERENCES hosted_agents(id) ON DELETE CASCADE,
    file_path VARCHAR(500) NOT NULL,
    file_type VARCHAR(20) NOT NULL DEFAULT 'text',
    content TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    truncated BOOLEAN NOT NULL DEFAULT FALSE,
    is_binary BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_agent_file UNIQUE (hosted_agent_id, file_path)
);
"""


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture
async def db_session(pg_container):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy import text

    pg_async_url = pg_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(pg_async_url, future=True)
    async with engine.begin() as conn:
        for stmt in MINIMAL_SCHEMA.split(";"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(text(stmt))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    # Truncate so each test sees a clean DB without dropping the schema
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE agent_files, hosted_agents, agents CASCADE"))
    await engine.dispose()


async def _create_hosted(db_session) -> tuple[str, str]:
    """Insert a hosted_agent row and return (hosted_id, owner_user_id)."""
    from sqlalchemy import text

    hosted_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO agents (id, name, handle) VALUES (:id, 'test', 'test-handle')"
        ),
        {"id": agent_id},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO hosted_agents (id, agent_id, owner_user_id, system_prompt, status)
            VALUES (:id, :aid, :oid, 'You are a test agent', 'running')
            """
        ),
        {"id": hosted_id, "aid": agent_id, "oid": owner_id},
    )
    await db_session.commit()
    return hosted_id, owner_id


def _make_service(repo):
    """Build a HostedAgentService with all external collaborators mocked.

    The service is wired with ``get_hosted_agent`` short-circuited because
    every public file method calls it for an ownership check; the file
    methods themselves are what we want to exercise here.
    """
    from app.services.hosted_agent_service import HostedAgentService

    svc = HostedAgentService(
        repo=repo,
        agent_service=MagicMock(),
        openrouter=MagicMock(),
        openviking=MagicMock(),
    )
    svc.runner_url = "http://runner.test"
    svc.settings = MagicMock()
    svc.settings.agent_runner_key = "test-key"
    svc.settings.agent_runner_url = "http://runner.test"
    svc.get_hosted_agent = AsyncMock(return_value={"id": "x", "status": "running"})
    return svc


@pytest.mark.asyncio
async def test_write_file_pushes_to_runner_disk(db_session, monkeypatch):
    """write_file must DB-commit AND HTTP PUT to the runner."""
    from app.repositories.hosted_agent_repo import HostedAgentRepository

    hosted_id, owner_id = await _create_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = _make_service(repo)

    sent: dict = {}

    async def fake_put(self, url, json=None, **kwargs):
        sent["url"] = url
        sent["json"] = json
        return httpx.Response(200, json={"status": "written"})

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    await svc.write_file(hosted_id, owner_id, "AGENT.md", "hello world")

    assert sent["url"] == f"http://runner.test/agents/{hosted_id}/files"
    assert sent["json"]["file_path"] == "AGENT.md"
    assert sent["json"]["content"] == "hello world"

    row = await repo.get_file(hosted_id, "AGENT.md")
    assert row["content"] == "hello world"
    assert row["version"] == 1


@pytest.mark.asyncio
async def test_write_file_bumps_version(db_session, monkeypatch):
    from app.repositories.hosted_agent_repo import HostedAgentRepository

    hosted_id, owner_id = await _create_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = _make_service(repo)

    monkeypatch.setattr(svc, "_push_file_to_runner", AsyncMock())

    await svc.write_file(hosted_id, owner_id, "x.md", "v1")
    await svc.write_file(hosted_id, owner_id, "x.md", "v2")
    await svc.write_file(hosted_id, owner_id, "x.md", "v3")

    row = await repo.get_file(hosted_id, "x.md")
    assert row["version"] == 3
    assert row["content"] == "v3"


@pytest.mark.asyncio
async def test_etag_conflict_raises_stale(db_session, monkeypatch):
    """Stale If-Match must raise StaleVersionError with current content."""
    from app.repositories.hosted_agent_repo import (
        HostedAgentRepository,
        StaleVersionError,
    )

    hosted_id, owner_id = await _create_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = _make_service(repo)

    monkeypatch.setattr(svc, "_push_file_to_runner", AsyncMock())

    await svc.write_file(hosted_id, owner_id, "x.md", "first")
    await svc.write_file(hosted_id, owner_id, "x.md", "second")  # version → 2

    with pytest.raises(StaleVersionError) as exc_info:
        # Client thinks version is 1, but disk is at 2 — conflict
        await svc.write_file(
            hosted_id, owner_id, "x.md", "third", if_match_version=1
        )

    assert exc_info.value.current_version == 2
    assert exc_info.value.current_content == "second"


@pytest.mark.asyncio
async def test_etag_match_succeeds(db_session, monkeypatch):
    from app.repositories.hosted_agent_repo import HostedAgentRepository

    hosted_id, owner_id = await _create_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = _make_service(repo)
    monkeypatch.setattr(svc, "_push_file_to_runner", AsyncMock())

    await svc.write_file(hosted_id, owner_id, "x.md", "first")
    row = await svc.write_file(
        hosted_id, owner_id, "x.md", "second", if_match_version=1
    )
    assert row["version"] == 2


@pytest.mark.asyncio
async def test_sync_prunes_ghost_files(db_session, monkeypatch):
    """Files removed by the agent on disk must disappear from DB on next sync."""
    from app.repositories.hosted_agent_repo import HostedAgentRepository

    hosted_id, _ = await _create_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = _make_service(repo)

    # Seed three files in DB (simulating a previous sync)
    await repo.upsert_file(hosted_id, "keep.md", "k", "text")
    await repo.upsert_file(hosted_id, "delete-me.md", "d", "text")
    await repo.upsert_file(hosted_id, "also-keep.md", "a", "text")

    # Mock runner /files returning only two of them — the agent deleted "delete-me.md"
    runner_response = {
        "files": [
            {"file_path": "keep.md", "content": "k-updated", "size_bytes": 9},
            {"file_path": "also-keep.md", "content": "a", "size_bytes": 1},
        ]
    }

    class FakeResp:
        status_code = 200

        def json(self):
            return runner_response

    async def fake_get(self, url, **kwargs):
        return FakeResp()

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    await svc._sync_files_from_runner(hosted_id)

    rows = await repo.list_files(hosted_id)
    paths = {r["file_path"] for r in rows}
    assert "keep.md" in paths
    assert "also-keep.md" in paths
    assert "delete-me.md" not in paths


@pytest.mark.asyncio
async def test_sync_empty_runner_response_does_not_wipe(db_session, monkeypatch):
    """Empty runner /files (transient error) must NOT wipe existing rows."""
    from app.repositories.hosted_agent_repo import HostedAgentRepository

    hosted_id, _ = await _create_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = _make_service(repo)

    await repo.upsert_file(hosted_id, "keep.md", "k", "text")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"files": []}

    async def fake_get(self, url, **kwargs):
        return FakeResp()

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    await svc._sync_files_from_runner(hosted_id)

    rows = await repo.list_files(hosted_id)
    assert {r["file_path"] for r in rows} == {"keep.md"}


@pytest.mark.asyncio
async def test_path_traversal_rejected(db_session):
    """Service-layer write_file must reject `../`, absolute paths, and NUL."""
    from app.repositories.hosted_agent_repo import HostedAgentRepository
    from fastapi import HTTPException

    hosted_id, owner_id = await _create_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = _make_service(repo)

    bad_paths = ["../etc/passwd", "/etc/passwd", "a/../../b", "x\x00.md", ""]
    for bad in bad_paths:
        with pytest.raises(HTTPException) as exc_info:
            await svc.write_file(hosted_id, owner_id, bad, "evil")
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_file_url_encodes_special_chars(db_session, monkeypatch):
    """delete_file must URL-quote spaces / unicode before calling the runner."""
    from app.repositories.hosted_agent_repo import HostedAgentRepository

    hosted_id, owner_id = await _create_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = _make_service(repo)

    await repo.upsert_file(hosted_id, "my notes/файл.md", "x", "text")

    captured: dict = {}

    async def fake_delete(self, url, **kwargs):
        captured["url"] = url
        return httpx.Response(200, json={"status": "deleted"})

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "delete", fake_delete)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    await svc.delete_file(hosted_id, owner_id, "my notes/файл.md")

    # Spaces must be %20, cyrillic must be percent-encoded utf-8, "/" must
    # be preserved so the runner path-router still matches.
    assert "my%20notes/" in captured["url"]
    assert "%D1%84" in captured["url"]  # 'ф' utf-8


@pytest.mark.asyncio
async def test_delete_subpath_file_succeeds(db_session, monkeypatch):
    """M2: delete_file must resolve correctly for paths with directory separators.

    Reproduces the bug where DELETE /files/notes/test.md returned 404
    while the file was visible via GET /files.  The service + repo layer must
    match on the exact file_path string including the slash.
    """
    from app.repositories.hosted_agent_repo import HostedAgentRepository

    hosted_id, owner_id = await _create_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = _make_service(repo)

    # Insert a file that lives in a subdirectory
    await repo.upsert_file(hosted_id, "notes/test.md", "hello", "text")

    # Confirm it is visible via list_files
    files_before = await repo.list_files(hosted_id)
    assert any(f["file_path"] == "notes/test.md" for f in files_before)

    # Patch out runner HTTP call — we're testing DB round-trip only
    monkeypatch.setattr(
        "httpx.AsyncClient.delete",
        AsyncMock(return_value=httpx.Response(200, json={"status": "deleted"})),
    )

    await svc.delete_file(hosted_id, owner_id, "notes/test.md")

    files_after = await repo.list_files(hosted_id)
    assert not any(f["file_path"] == "notes/test.md" for f in files_after)


@pytest.mark.asyncio
async def test_delete_nonexistent_subpath_raises_404(db_session):
    """Deleting a subpath file that doesn't exist must return 404, not 500."""
    from app.repositories.hosted_agent_repo import HostedAgentRepository
    from fastapi import HTTPException

    hosted_id, owner_id = await _create_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = _make_service(repo)

    with pytest.raises(HTTPException) as exc_info:
        await svc.delete_file(hosted_id, owner_id, "deep/nested/missing.md")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_batch_write_atomic_on_failure(db_session, monkeypatch):
    """A failure mid-batch must roll back rows that were just created."""
    from app.repositories.hosted_agent_repo import HostedAgentRepository

    hosted_id, owner_id = await _create_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = _make_service(repo)
    monkeypatch.setattr(svc, "_push_file_to_runner", AsyncMock())

    # Make the third upsert fail
    real_upsert = repo.upsert_file
    call_count = {"n": 0}

    async def flaky_upsert(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise RuntimeError("synthetic DB failure")
        return await real_upsert(*args, **kwargs)

    monkeypatch.setattr(repo, "upsert_file", flaky_upsert)

    items = [
        {"file_path": "a.md", "content": "a", "file_type": "text"},
        {"file_path": "b.md", "content": "b", "file_type": "text"},
        {"file_path": "c.md", "content": "c", "file_type": "text"},
    ]

    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await svc.write_files_batch(hosted_id, owner_id, items)

    # Rollback: only newly-created rows should be removed. Both a.md and
    # b.md were just created in this call, so both should be gone.
    rows = await repo.list_files(hosted_id)
    paths = {r["file_path"] for r in rows}
    assert "a.md" not in paths
    assert "b.md" not in paths


@pytest.mark.asyncio
async def test_batch_write_runner_failures_reported_per_file(db_session, monkeypatch):
    """Batch write should report per-file runner push failures without aborting."""
    from app.repositories.hosted_agent_repo import HostedAgentRepository

    hosted_id, owner_id = await _create_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = _make_service(repo)

    async def flaky_push(hid, file_path, content):
        if file_path == "b.md":
            raise RuntimeError("runner timeout")

    monkeypatch.setattr(svc, "_push_file_to_runner", flaky_push)

    items = [
        {"file_path": "a.md", "content": "a", "file_type": "text"},
        {"file_path": "b.md", "content": "b", "file_type": "text"},
    ]
    written, failed = await svc.write_files_batch(hosted_id, owner_id, items)

    # DB succeeded for both
    assert len(written) == 2
    # Runner push reports b.md failure
    assert len(failed) == 1
    assert failed[0]["file_path"] == "b.md"


def test_parse_if_match_header_accepts_quoted_and_bare():
    from app.api.v1.hosted_agents import _parse_if_match

    assert _parse_if_match(None) is None
    assert _parse_if_match('"v3"') == 3
    assert _parse_if_match("v3") == 3
    assert _parse_if_match('W/"v7"') == 7


def test_parse_if_match_rejects_wildcard_and_garbage():
    from app.api.v1.hosted_agents import _parse_if_match
    from fastapi import HTTPException

    for bad in ["*", "garbage", '"abc"', "vX"]:
        with pytest.raises(HTTPException):
            _parse_if_match(bad)

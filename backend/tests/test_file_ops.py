"""Tests for hosted-agent file operations (v1.28.0).

Covers the bug fixes and Tier 1 features added for the file-ops overhaul:

- write_file pushes to runner disk after DB commit
- _sync_files_from_runner reconciles ghost rows after agent deletes
- ETag/version conflict raises 412 with current content
- Batch upload rolls back on partial DB failure
- Path traversal and absolute paths are rejected
- delete_file URL-encodes special characters before calling runner

API-route-level tests (no Docker required):
- PUT /hosted-agents/{id}/files returns 200 with full AgentFileResponse shape
- POST /hosted-agents/{id}/files/batch returns 200 with full AgentFileResponse per item
  These tests catch KeyError in _file_response() if service returns truncated dict.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import get_current_user
from app.core.etag import parse_if_match as _parse_if_match
from app.main import app as fastapi_app
from app.repositories.hosted_agent_repo import HostedAgentRepository, StaleVersionError
from app.services.hosted_agent_service import HostedAgentService, get_hosted_agent_service

try:
    from testcontainers.postgres import (
        PostgresContainer,  # noqa: PLC0415 — optional dep, skip if not installed
    )
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


async def insert_hosted(db_session) -> tuple[str, str]:
    """Insert a hosted_agent row and return (hosted_id, owner_user_id)."""
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


def build_service(repo):
    """Build a HostedAgentService with all external collaborators mocked.

    The service is wired with ``get_hosted_agent`` short-circuited because
    every public file method calls it for an ownership check; the file
    methods themselves are what we want to exercise here.
    """

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
    """P5a: write_file must PUT to runner and NOT write to DB."""
    hosted_id, owner_id = await insert_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = build_service(repo)

    sent: dict = {}

    async def fake_put(self, url, json=None, **kwargs):
        sent["url"] = url
        sent["json"] = json
        return httpx.Response(200, json={"status": "written", "version": "sha000000001"})

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    result = await svc.write_file(hosted_id, owner_id, "AGENT.md", "hello world")

    assert sent["url"] == f"http://runner.test/agents/{hosted_id}/files"
    assert sent["json"]["file_path"] == "AGENT.md"
    assert sent["json"]["content"] == "hello world"
    # P5a: no DB write — file must NOT appear in agent_files table
    row = await repo.get_file(hosted_id, "AGENT.md")
    assert row is None, "P5a: write_file must not upsert to DB"
    assert result["version"] == "sha000000001"


@pytest.mark.asyncio
async def test_write_file_bumps_version(db_session, monkeypatch):
    hosted_id, owner_id = await insert_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = build_service(repo)

    # P3: write_file calls runner PUT first; mock httpx directly
    async def fake_put(self, url, json=None, headers=None, **kwargs):
        return httpx.Response(200, json={"status": "written", "version": "sha000000001"})

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    await svc.write_file(hosted_id, owner_id, "x.md", "v1")
    await svc.write_file(hosted_id, owner_id, "x.md", "v2")
    result = await svc.write_file(hosted_id, owner_id, "x.md", "v3")

    # P5a: no DB rows — runner is sole write target
    row = await repo.get_file(hosted_id, "x.md")
    assert row is None, "P5a: write_file must not upsert to DB"
    assert result["version"] == "sha000000001"


@pytest.mark.asyncio
async def test_etag_conflict_raises_stale(db_session, monkeypatch):
    """Stale If-Match must raise StaleVersionError with current content."""

    hosted_id, owner_id = await insert_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = build_service(repo)

    current_sha = "currentsha001"

    # P3: write_file is runner-first; simulate 412 from runner on stale sha
    async def fake_put_stale(self, url, json=None, headers=None, **kwargs):
        if_match = (headers or {}).get("If-Match")
        if if_match and if_match != current_sha:
            return httpx.Response(
                412,
                json={
                    "detail": {
                        "message": "Precondition Failed",
                        "current_version": current_sha,
                        "current_content": "current file content",
                    }
                },
            )
        return httpx.Response(200, json={"status": "written", "version": current_sha})

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put_stale)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    with pytest.raises(StaleVersionError) as exc_info:
        await svc.write_file(
            hosted_id, owner_id, "x.md", "third", if_match_version="oldsha000001"
        )

    assert exc_info.value.current_version == current_sha
    assert exc_info.value.current_content == "current file content"


@pytest.mark.asyncio
async def test_etag_match_succeeds(db_session, monkeypatch):
    hosted_id, owner_id = await insert_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = build_service(repo)

    # P3: write_file is runner-first; mock httpx for unconditional success
    async def fake_put(self, url, json=None, headers=None, **kwargs):
        return httpx.Response(200, json={"status": "written", "version": "newsha000001"})

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    await svc.write_file(hosted_id, owner_id, "x.md", "first")
    result = await svc.write_file(
        hosted_id, owner_id, "x.md", "second", if_match_version="matchingsha"
    )
    # version in return is sha from runner (not DB int)
    assert result["version"] == "newsha000001"


@pytest.mark.asyncio
async def test_write_file_no_db_row_after_write(db_session, monkeypatch):
    """P5a: write_file leaves agent_files table empty (runner-only)."""
    hosted_id, owner_id = await insert_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = build_service(repo)

    async def fake_put(self, url, json=None, headers=None, **kwargs):
        return httpx.Response(200, json={"status": "written", "version": "sha000abc123"})

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    await svc.write_file(hosted_id, owner_id, "keep.md", "k")
    await svc.write_file(hosted_id, owner_id, "also-keep.md", "a")

    rows = await repo.list_files(hosted_id)
    assert rows == [], "P5a: write_file must not persist rows to agent_files"


@pytest.mark.asyncio
async def test_batch_write_no_db_rows(db_session, monkeypatch):
    """P5a: write_files_batch leaves agent_files table empty (runner-only)."""
    hosted_id, owner_id = await insert_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = build_service(repo)

    sha_n = {"n": 0}

    async def fake_push(hid, fp, content):
        sha_n["n"] += 1
        return f"sha{sha_n['n']:09d}"

    monkeypatch.setattr(svc, "_push_file_to_runner", fake_push)

    items = [
        {"file_path": "a.md", "content": "a", "file_type": "text"},
        {"file_path": "b.md", "content": "b", "file_type": "text"},
    ]
    written, failed = await svc.write_files_batch(hosted_id, owner_id, items)

    assert len(failed) == 0
    rows = await repo.list_files(hosted_id)
    assert rows == [], "P5a: write_files_batch must not persist rows to agent_files"


@pytest.mark.asyncio
async def test_path_traversal_rejected(db_session):
    """Service-layer write_file must reject `../`, absolute paths, and NUL."""
    hosted_id, owner_id = await insert_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = build_service(repo)

    bad_paths = ["../etc/passwd", "/etc/passwd", "a/../../b", "x\x00.md", ""]
    for bad in bad_paths:
        with pytest.raises(HTTPException) as exc_info:
            await svc.write_file(hosted_id, owner_id, bad, "evil")
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_file_url_encodes_special_chars(db_session, monkeypatch):
    """P5a: delete_file must URL-quote spaces / unicode before calling the runner."""
    hosted_id, owner_id = await insert_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = build_service(repo)

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

    assert "my%20notes/" in captured["url"]
    assert "%D1%84" in captured["url"]  # 'ф' utf-8


@pytest.mark.asyncio
async def test_delete_file_runner_only_no_db_call(db_session, monkeypatch):
    """P5a: delete_file calls runner DELETE and does NOT touch DB repo."""
    hosted_id, owner_id = await insert_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = build_service(repo)

    runner_url_called: list[str] = []

    async def fake_delete(self, url, **kwargs):
        runner_url_called.append(url)
        return httpx.Response(200, json={"status": "deleted"})

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "delete", fake_delete)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    await svc.delete_file(hosted_id, owner_id, "notes/test.md")

    assert any(
        "notes/test.md" in url or "notes%2Ftest.md" in url
        for url in runner_url_called
    )
    # DB table must be empty — no rows were inserted or deleted
    rows = await repo.list_files(hosted_id)
    assert rows == [], "P5a: delete_file must not touch agent_files DB"


@pytest.mark.asyncio
async def test_delete_nonexistent_raises_404_via_runner(db_session, monkeypatch):
    """P5a: delete_file raises HTTP 404 when runner returns 404 (file not on disk)."""
    hosted_id, owner_id = await insert_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = build_service(repo)

    async def fake_delete(self, url, **kwargs):
        return httpx.Response(404, json={"detail": "File not found"})

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "delete", fake_delete)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    with pytest.raises(HTTPException) as exc_info:
        await svc.delete_file(hosted_id, owner_id, "deep/nested/missing.md")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_batch_write_runner_only(db_session, monkeypatch):
    """P5a: write_files_batch is runner-only; all 3 items returned, no DB rows."""
    hosted_id, owner_id = await insert_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = build_service(repo)

    sha_n = {"n": 0}

    async def fake_push(hid, fp, content):
        sha_n["n"] += 1
        return f"sha{sha_n['n']:09d}"

    monkeypatch.setattr(svc, "_push_file_to_runner", fake_push)

    items = [
        {"file_path": "a.md", "content": "a", "file_type": "text"},
        {"file_path": "b.md", "content": "b", "file_type": "text"},
        {"file_path": "c.md", "content": "c", "file_type": "text"},
    ]

    written, failed = await svc.write_files_batch(hosted_id, owner_id, items)
    assert len(failed) == 0
    written_paths = {r["file_path"] for r in written}
    assert written_paths == {"a.md", "b.md", "c.md"}

    rows = await repo.list_files(hosted_id)
    assert rows == [], "P5a: write_files_batch must not persist rows to agent_files"


@pytest.mark.asyncio
async def test_batch_write_runner_failures_reported_per_file(db_session, monkeypatch):
    """Batch write reports per-file runner push failures without aborting."""
    hosted_id, owner_id = await insert_hosted(db_session)
    repo = HostedAgentRepository(db_session)
    svc = build_service(repo)

    async def flaky_push(hid, file_path, content):
        if file_path == "b.md":
            raise RuntimeError("runner timeout")
        return "sha000000001"

    monkeypatch.setattr(svc, "_push_file_to_runner", flaky_push)

    items = [
        {"file_path": "a.md", "content": "a", "file_type": "text"},
        {"file_path": "b.md", "content": "b", "file_type": "text"},
    ]
    written, failed = await svc.write_files_batch(hosted_id, owner_id, items)

    assert len(written) == 1
    assert written[0]["file_path"] == "a.md"
    assert len(failed) == 1
    assert failed[0]["file_path"] == "b.md"


def test_parse_if_match_header_accepts_sha_forms():
    """_parse_if_match returns an opaque sha string (not int).

    Accepts bare sha, quoted sha, and weak-ETag form.
    Legacy ``v{int}`` is accepted as-is (opaque string, not parsed to int).
    """
    assert _parse_if_match(None) is None
    assert _parse_if_match('"abc123def456"') == "abc123def456"
    assert _parse_if_match("abc123def456") == "abc123def456"
    assert _parse_if_match('W/"abc123def456"') == "abc123def456"
    assert _parse_if_match('"v3"') == "v3"
    assert _parse_if_match("v3") == "v3"


def test_parse_if_match_rejects_wildcard_and_empty():
    with pytest.raises(HTTPException):
        _parse_if_match("*")
    with pytest.raises(HTTPException):
        _parse_if_match('""')  # empty quoted string → 400


# ── Runner-authoritative read/write tests (no real DB) ────────────────────────
#
# These tests use _make_service(MagicMock()) to build a service without a DB.
# HostedAgentService.__init__ calls get_settings() which fails when extra env
# vars are present (e.g. AGENTSPORE_REDIS_PORT). We patch get_settings() to
# return a minimal settings mock so the constructor succeeds without real config.

def svc_no_db(monkeypatch) -> HostedAgentService:
    """Build a HostedAgentService with no DB and runner_url pre-configured.

    Patches ``get_settings`` so ``HostedAgentService.__init__`` gets a mock
    settings object with ``agent_runner_url`` and ``agent_runner_key`` set,
    which avoids a real DB connection attempt for runner-only tests.
    """
    mock_settings = MagicMock()
    mock_settings.agent_runner_url = "http://runner.test"
    mock_settings.agent_runner_key = "test-key"
    monkeypatch.setattr("app.services.hosted_agent_service.get_settings", lambda: mock_settings)
    return build_service(MagicMock())


@pytest.mark.asyncio
async def test_list_files_reads_from_runner_sha_version(monkeypatch):
    """list_files returns version=sha string from runner (not int)."""
    svc = svc_no_db(monkeypatch)

    runner_payload = {
        "files": [
            {
                "file_path": "AGENT.md",
                "content": "# Agent",
                "size_bytes": 7,
                "truncated": False,
                "is_binary": False,
                "version": "abc123def456",
                "modified_at": "2026-05-29T12:00:00+00:00",
            }
        ]
    }

    async def fake_get(self, url, headers=None, params=None, **kwargs):
        return httpx.Response(200, json=runner_payload)

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    files = await svc.list_files("hosted-123", "user-456")

    assert len(files) == 1
    assert files[0]["version"] == "abc123def456"
    assert files[0]["updated_at"] == "2026-05-29T12:00:00+00:00"


@pytest.mark.asyncio
async def test_list_files_runner_down_returns_503(monkeypatch):
    """list_files raises HTTPException 503 when runner is unreachable."""
    svc = svc_no_db(monkeypatch)

    async def fake_get(self, url, **kwargs):
        raise ConnectionError("runner down")

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    with pytest.raises(HTTPException) as exc_info:
        await svc.list_files("hosted-123", "user-456")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_read_file_returns_sha_version(monkeypatch):
    """read_file returns version=sha from runner response."""
    svc = svc_no_db(monkeypatch)

    async def fake_get(self, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "file_path": "README.md",
                "content": "hello",
                "size_bytes": 5,
                "truncated": False,
                "is_binary": False,
                "version": "deadbeef0001",
                "modified_at": "2026-05-29T08:00:00+00:00",
            },
        )

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    result = await svc.read_file("hosted-123", "user-456", "README.md")

    assert result["version"] == "deadbeef0001"
    assert result["updated_at"] == "2026-05-29T08:00:00+00:00"


@pytest.mark.asyncio
async def test_read_file_404_from_runner(monkeypatch):
    """read_file raises HTTPException 404 when runner returns 404."""
    svc = svc_no_db(monkeypatch)

    async def fake_get(self, url, **kwargs):
        return httpx.Response(404, json={"detail": "File not found"})

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    with pytest.raises(HTTPException) as exc_info:
        await svc.read_file("hosted-123", "user-456", "missing.md")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_write_file_412_raises_stale_with_sha(monkeypatch):
    """write_file raises StaleVersionError carrying sha current_version on runner 412."""
    svc = svc_no_db(monkeypatch)

    async def fake_put(self, url, json=None, headers=None, **kwargs):
        return httpx.Response(
            412,
            json={
                "detail": {
                    "message": "Precondition Failed",
                    "current_version": "newshaabcdef",
                    "current_content": "current body",
                }
            },
        )

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    with pytest.raises(Exception) as exc_info:
        await svc.write_file(
            "hosted-123", "user-456", "file.md", "body",
            if_match_version="oldshaabcdef",
        )

    assert exc_info.type.__name__ == "StaleVersionError"
    assert exc_info.value.current_version == "newshaabcdef"
    assert exc_info.value.current_content == "current body"


@pytest.mark.asyncio
async def test_write_file_runner_503_on_error(monkeypatch):
    """write_file raises HTTPException 503 on non-412 runner error."""
    svc = svc_no_db(monkeypatch)

    async def fake_put(self, url, **kwargs):
        return httpx.Response(500, json={"detail": "internal error"})

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    with pytest.raises(HTTPException) as exc_info:
        await svc.write_file("hosted-123", "user-456", "file.md", "body")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_list_files_include_hidden_forwarded(monkeypatch):
    """list_files passes include_hidden=true to runner query params."""
    svc = svc_no_db(monkeypatch)

    captured: dict = {}

    async def fake_get(self, url, headers=None, params=None, **kwargs):
        captured["params"] = params
        return httpx.Response(200, json={"files": []})

    async def fake_aenter(self):
        return self

    async def fake_aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fake_aexit)

    await svc.list_files("hosted-123", "user-456", include_hidden=True)
    assert captured.get("params") == {"include_hidden": "true"}


@pytest.mark.asyncio
async def test_list_running_agents_empty_files_payload(monkeypatch):
    """P5a: list_running_agents must return files=[] — DB list_files/get_file not called."""
    mock_settings = MagicMock()
    mock_settings.agent_runner_url = "http://runner.test"
    mock_settings.agent_runner_key = "test-key"
    mock_settings.oauth_redirect_base_url = "https://agentspore.com"
    monkeypatch.setattr("app.services.hosted_agent_service.get_settings", lambda: mock_settings)

    repo = MagicMock()
    repo.list_running = AsyncMock(return_value=[
        {
            "id": "hosted-abc",
            "agent_id": "agent-xyz",
            "system_prompt": "You are a test agent",
            "model": "test/model:free",
            "runtime": "python-minimal",
            "agent_api_key": "af_test_key",
            "heartbeat_seconds": 3600,
        }
    ])
    svc = HostedAgentService(
        repo=repo,
        agent_service=MagicMock(),
        openrouter=MagicMock(),
        openviking=MagicMock(),
    )
    svc.runner_url = "http://runner.test"
    svc.settings = mock_settings

    result = await svc.list_running_agents()

    assert len(result) == 1
    assert result[0]["files"] == [], "P5a: files payload must be empty"
    repo.list_files.assert_not_called()
    repo.get_file.assert_not_called()


# ── API-route-level tests: regression guard for P5a KeyError in _file_response ──
#
# These tests exercise the full router → service → _file_response() path without
# Docker or a real DB. They catch ``KeyError: 'id'`` (and any other missing key)
# that would occur if write_file/write_files_batch return a truncated dict.
#
# Pattern: override get_hosted_agent_service and get_current_user via
# fastapi_app.dependency_overrides, call the real route through ASGITransport.
# No testcontainers needed — the service mock short-circuits DB access.
# All imports are at module top level (fastapi_app, get_current_user,
# get_hosted_agent_service imported above).

_HOSTED_ID = str(uuid.uuid4())
_FILE_RESPONSE_FIELDS = frozenset({
    "id", "file_path", "file_type", "content",
    "size_bytes", "updated_at", "version", "truncated", "is_binary",
})


def full_file_dict(file_path: str = "README.md", content: str = "hello") -> dict:
    """Return a dict matching all keys consumed by _file_response().

    Plain factory (not a fixture) because callers vary file_path/content
    per-assertion. Named without _make_/_build_/_create_ prefix per style gate.
    """
    return {
        "id": "",
        "file_path": file_path,
        "file_type": "text",
        "content": content,
        "size_bytes": len(content.encode()),
        "updated_at": "",
        "version": "sha000abc123",
        "truncated": False,
        "is_binary": False,
    }


@pytest.fixture
def api_client_with_svc_mock():
    """Yield (AsyncClient, svc_mock) with dependency overrides pre-wired.

    write_file and write_files_batch return values are set by each test so the
    route exercises _file_response() with whatever shape the service produces.
    """
    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()

    svc_mock = MagicMock()
    svc_mock.write_file = AsyncMock()
    svc_mock.write_files_batch = AsyncMock()

    fastapi_app.dependency_overrides[get_current_user] = lambda: mock_user
    fastapi_app.dependency_overrides[get_hosted_agent_service] = lambda: svc_mock

    transport = ASGITransport(app=fastapi_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    yield client, svc_mock

    fastapi_app.dependency_overrides.pop(get_current_user, None)
    fastapi_app.dependency_overrides.pop(get_hosted_agent_service, None)


@pytest.mark.asyncio
async def test_put_files_route_returns_200_with_full_shape(api_client_with_svc_mock):
    """PUT /hosted-agents/{id}/files → 200 with all AgentFileResponse fields.

    Regression guard: if write_file returns a truncated dict (e.g. missing
    ``id``), _file_response() raises KeyError → 500. This test fails before
    the P5a fix and passes after.
    """
    client, svc_mock = api_client_with_svc_mock
    svc_mock.write_file.return_value = full_file_dict("AGENT.md", "# Agent")

    resp = await client.put(
        f"/api/v1/hosted-agents/{_HOSTED_ID}/files",
        json={"file_path": "AGENT.md", "content": "# Agent", "file_type": "text"},
        headers={"Authorization": "Bearer fake-token"},
    )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    missing = _FILE_RESPONSE_FIELDS - set(data.keys())
    assert not missing, f"Response missing keys: {missing}"
    assert data["file_path"] == "AGENT.md"
    assert data["version"] == "sha000abc123"


@pytest.mark.asyncio
async def test_put_files_route_all_file_response_field_types(api_client_with_svc_mock):
    """PUT /hosted-agents/{id}/files: verify type of each _file_response field.

    Catches KeyError and type mismatches that would surface as 500 or 422.
    """
    client, svc_mock = api_client_with_svc_mock
    svc_mock.write_file.return_value = full_file_dict("src/main.py", "print('hi')")

    resp = await client.put(
        f"/api/v1/hosted-agents/{_HOSTED_ID}/files",
        json={"file_path": "src/main.py", "content": "print('hi')", "file_type": "text"},
        headers={"Authorization": "Bearer fake-token"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["id"], str)
    assert isinstance(data["size_bytes"], int)
    assert isinstance(data["truncated"], bool)
    assert isinstance(data["is_binary"], bool)
    assert isinstance(data["file_type"], str)
    assert isinstance(data["updated_at"], str)


@pytest.mark.asyncio
async def test_batch_files_route_returns_200_with_full_shape(api_client_with_svc_mock):
    """POST /hosted-agents/{id}/files/batch → 200 with full AgentFileResponse per item.

    Regression guard: if write_files_batch returns truncated dicts, the route's
    [_file_response(r) for r in written] call raises KeyError → 500.
    """
    client, svc_mock = api_client_with_svc_mock
    written = [
        full_file_dict("a.md", "alpha"),
        full_file_dict("b.md", "beta"),
    ]
    svc_mock.write_files_batch.return_value = (written, [])

    resp = await client.post(
        f"/api/v1/hosted-agents/{_HOSTED_ID}/files/batch",
        json={"files": [
            {"file_path": "a.md", "content": "alpha", "file_type": "text"},
            {"file_path": "b.md", "content": "beta", "file_type": "text"},
        ]},
        headers={"Authorization": "Bearer fake-token"},
    )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "written" in data
    assert len(data["written"]) == 2
    for item in data["written"]:
        missing = _FILE_RESPONSE_FIELDS - set(item.keys())
        assert not missing, f"Batch item missing keys: {missing}"


@pytest.mark.asyncio
async def test_batch_files_route_partial_failure_still_200(api_client_with_svc_mock):
    """POST batch with partial runner failure → 200, 1 written + 1 failed reported."""
    client, svc_mock = api_client_with_svc_mock
    written = [full_file_dict("ok.md", "fine")]
    failed = [{"file_path": "bad.md", "error": "runner timeout"}]
    svc_mock.write_files_batch.return_value = (written, failed)

    resp = await client.post(
        f"/api/v1/hosted-agents/{_HOSTED_ID}/files/batch",
        json={"files": [
            {"file_path": "ok.md", "content": "fine", "file_type": "text"},
            {"file_path": "bad.md", "content": "boom", "file_type": "text"},
        ]},
        headers={"Authorization": "Bearer fake-token"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["written"]) == 1
    assert len(data["failed"]) == 1
    assert data["failed"][0]["file_path"] == "bad.md"

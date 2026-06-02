"""Unit tests for P5a fork — runner-only, no DB fallback (no testcontainers required).

Covers:
  - _fork_read_source_files: uses runner GET when dir non-empty
  - _fork_read_source_files: returns [] on empty runner dir (P5a: no DB fallback)
  - _fork_read_source_files: returns [] on runner error (P5a: no DB fallback)
  - _fork_seed_new_agent: POSTs to runner /files/import with correct payload
  - _fork_seed_new_agent: applies AGENT.md / MEMORY.md transformations
  - list_files_with_content repo method: REMOVED in P5b (V64 drops agent_files table)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.repositories.hosted_agent_repo import HostedAgentRepository
from app.services.hosted_agent_service import HostedAgentService
from app.services.runner_client import RunnerFileClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service_factory():
    """Return a factory that builds a HostedAgentService with injected mocks.

    Usage::

        def test_foo(service_factory):
            svc = service_factory(repo=repo_mock, runner_url="http://runner:8080")
    """

    def _create(
        *,
        repo: MagicMock | None = None,
        runner_url: str | None = "http://runner:8080",
        runner_key: str = "test-runner-key",
    ) -> HostedAgentService:
        settings_mock = MagicMock()
        settings_mock.agent_runner_url = runner_url
        settings_mock.agent_runner_key = runner_key
        settings_mock.max_hosted_agents_per_user = 10

        agent_svc_mock = AsyncMock()
        agent_svc_mock.register_agent = AsyncMock(
            return_value={
                "agent_id": str(uuid.uuid4()),
                "handle": "fork-handle",
                "api_key": "af_fork_test",
            }
        )

        svc = HostedAgentService.__new__(HostedAgentService)
        svc.repo = repo or MagicMock()
        svc.agent_svc = agent_svc_mock
        svc.openrouter = AsyncMock()
        svc.openviking = AsyncMock()
        svc.settings = settings_mock
        svc.runner_url = runner_url
        svc._rc = RunnerFileClient(runner_url=runner_url, runner_key=runner_key)
        return svc

    return _create


# ---------------------------------------------------------------------------
# _fork_read_source_files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fork_read_source_files_uses_runner_when_non_empty(service_factory):
    """Runner GET returns files → used directly, DB not called."""
    runner_files = [
        {"file_path": "AGENT.md", "content": "source prompt"},
        {"file_path": "agent.yaml", "content": "include_todo: true"},
        {"file_path": ".deep/memory/MEMORY.md", "content": "# Old memory"},
    ]

    repo_mock = MagicMock()
    repo_mock.list_files_with_content = AsyncMock()
    svc = service_factory(repo=repo_mock)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={"files": runner_files})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await svc._fork_read_source_files(
            source_hosted_id="source-id",
            source_name="SourceBot",
        )

    assert len(result) == 3
    paths = {f["file_path"] for f in result}
    assert "AGENT.md" in paths
    assert ".deep/memory/MEMORY.md" in paths
    # DB fallback NOT used
    repo_mock.list_files_with_content.assert_not_called()


@pytest.mark.asyncio
async def test_fork_read_source_files_empty_runner_dir_returns_empty(
    service_factory,
):
    """P5a: runner returns empty list → empty workspace fork (no DB fallback)."""
    repo_mock = MagicMock()
    repo_mock.list_files_with_content = AsyncMock()
    svc = service_factory(repo=repo_mock)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={"files": []})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await svc._fork_read_source_files(
            source_hosted_id="source-id",
            source_name="SourceBot",
        )

    # P5a: no DB fallback; empty dir → empty fork workspace
    assert result == []
    repo_mock.list_files_with_content.assert_not_called()


@pytest.mark.asyncio
async def test_fork_read_source_files_runner_error_returns_empty(
    service_factory,
):
    """P5a: runner returns non-200 → empty list (no DB fallback)."""
    repo_mock = MagicMock()
    repo_mock.list_files_with_content = AsyncMock()
    svc = service_factory(repo=repo_mock)

    mock_response = MagicMock()
    mock_response.status_code = 503

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await svc._fork_read_source_files(
            source_hosted_id="source-id",
            source_name="SourceBot",
        )

    assert result == []
    repo_mock.list_files_with_content.assert_not_called()


@pytest.mark.asyncio
async def test_fork_read_source_files_runner_unreachable_returns_empty(
    service_factory,
):
    """P5a: runner raises RequestError → empty list (no DB fallback)."""
    repo_mock = MagicMock()
    repo_mock.list_files_with_content = AsyncMock()
    svc = service_factory(repo=repo_mock)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client_cls.return_value = mock_client

        result = await svc._fork_read_source_files(
            source_hosted_id="source-id",
            source_name="SourceBot",
        )

    assert result == []
    repo_mock.list_files_with_content.assert_not_called()


# ---------------------------------------------------------------------------
# _fork_seed_new_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fork_seed_new_agent_posts_import_with_transformed_files(service_factory):
    """Runner POST /import called with AGENT.md replaced and MEMORY.md prefixed."""
    source_files = [
        {"file_path": "AGENT.md", "content": "original prompt", "file_type": "config"},
        {"file_path": ".deep/memory/MEMORY.md", "content": "# Old mem", "file_type": "memory"},
        {"file_path": "agent.yaml", "content": "x: 1", "file_type": "config"},
    ]

    svc = service_factory()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={"imported": 3})

    captured: list[dict] = []

    async def _fake_post(url, *, json, headers):
        captured.append(json)
        return mock_response

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = _fake_post
        mock_client_cls.return_value = mock_client

        await svc._fork_seed_new_agent(
            hosted_id="new-id",
            source_files=source_files,
            source_name="SourceBot",
            source_handle="sourcebot",
            system_prompt="New fork prompt",
        )

    assert len(captured) == 1
    items = {item["file_path"]: item["content"] for item in captured[0]["files"]}

    # AGENT.md: old content replaced; fork system_prompt + lineage present
    assert "original prompt" not in items["AGENT.md"]
    assert "New fork prompt" in items["AGENT.md"]
    assert "SourceBot" in items["AGENT.md"]
    assert "sourcebot" in items["AGENT.md"]

    # MEMORY.md: prepended with fork note, original content preserved
    assert items[".deep/memory/MEMORY.md"].startswith("# Memory\n\nForked from SourceBot.")
    assert "# Old mem" in items[".deep/memory/MEMORY.md"]

    # Other files pass through unchanged
    assert items["agent.yaml"] == "x: 1"


@pytest.mark.asyncio
async def test_fork_seed_agent_md_fully_replaced(service_factory):
    """AGENT.md old content is NOT included in fork output."""
    source_files = [
        {"file_path": "AGENT.md", "content": "SECRET_OLD_CONTENT", "file_type": "config"},
    ]
    svc = service_factory()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={"imported": 1})

    captured: list[dict] = []

    async def _fake_post(url, *, json, headers):
        captured.append(json)
        return mock_response

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = _fake_post
        mock_client_cls.return_value = mock_client

        await svc._fork_seed_new_agent(
            hosted_id="new-id2",
            source_files=source_files,
            source_name="Src",
            source_handle="src",
            system_prompt="Replacement prompt",
        )

    item = captured[0]["files"][0]
    assert item["file_path"] == "AGENT.md"
    assert "SECRET_OLD_CONTENT" not in item["content"]
    assert "Replacement prompt" in item["content"]


@pytest.mark.asyncio
async def test_fork_seed_no_runner_url_skips_without_exception(service_factory):
    """runner_url=None → seed skips silently (no exception raised)."""
    svc = service_factory(runner_url=None)
    source_files = [
        {"file_path": "AGENT.md", "content": "x", "file_type": "config"},
    ]
    # Must not raise.
    await svc._fork_seed_new_agent(
        hosted_id="new-id3",
        source_files=source_files,
        source_name="Src",
        source_handle="src",
        system_prompt="prompt",
    )


# ---------------------------------------------------------------------------
# Path-validation guard in _fork_seed_new_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fork_seed_rejects_traversal_paths(service_factory):
    """Source files with ``..`` segments are skipped; clean files are seeded normally."""
    captured: list[dict] = []

    async def _mock_post(url, *, json, headers):
        captured.append(json)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"imported": len(json["files"])}
        return resp

    svc = service_factory()
    source_files = [
        {"file_path": "../escape.txt", "content": "should be dropped"},
        {"file_path": "AGENT.md", "content": "clean file"},
    ]

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=_mock_post)
        mock_client_cls.return_value = mock_client

        await svc._fork_seed_new_agent(
            hosted_id="new-trav",
            source_files=source_files,
            source_name="Src",
            source_handle="src",
            system_prompt="prompt",
        )

    assert len(captured) == 1
    seeded_paths = [item["file_path"] for item in captured[0]["files"]]
    # Traversal path must be dropped; clean AGENT.md must survive.
    assert "../escape.txt" not in seeded_paths
    assert "AGENT.md" in seeded_paths


@pytest.mark.asyncio
async def test_fork_seed_handles_none_content(service_factory):
    """Source file with ``content: None`` is written as empty string, not literal 'None'."""
    captured: list[dict] = []

    async def _mock_post(url, *, json, headers):
        captured.append(json)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"imported": len(json["files"])}
        return resp

    svc = service_factory()
    source_files = [
        {"file_path": "AGENT.md", "content": None},
        {"file_path": "notes.md", "content": None},
    ]

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=_mock_post)
        mock_client_cls.return_value = mock_client

        await svc._fork_seed_new_agent(
            hosted_id="new-none",
            source_files=source_files,
            source_name="Src",
            source_handle="src",
            system_prompt="replacement",
        )

    assert len(captured) == 1
    for item in captured[0]["files"]:
        assert item["content"] != "None", (
            f"file_path={item['file_path']} has literal string 'None' — "
            "should be empty string or valid content"
        )
        assert isinstance(item["content"], str)


# ---------------------------------------------------------------------------
# list_files_with_content removed in P5b (V64 drops agent_files table)
# ---------------------------------------------------------------------------


def test_list_files_with_content_repo_method_removed():
    """HostedAgentRepository.list_files_with_content must be absent in P5b+.

    The agent_files table was dropped in V64. Runner workspace is the sole
    source of truth. This gate prevents re-introducing a DB fallback path.
    """
    assert not hasattr(HostedAgentRepository, "list_files_with_content"), (
        "list_files_with_content still exists on HostedAgentRepository — "
        "it must be removed: agent_files table is gone (V64, P5b)"
    )

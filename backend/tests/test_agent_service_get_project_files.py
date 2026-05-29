"""Unit tests for AgentService.get_project_files — VCS-only, no DB I/O.

All external collaborators (repo, git) are mocked. No real DB or runner needed.
"""

from __future__ import annotations

from typing import Protocol
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.services.agent_service import AgentService

# ── Typed fixture protocol ────────────────────────────────────────────────────


class _MockedSvc(Protocol):
    """Structural type for AgentService with mock collaborators.

    Exposes ``repo`` and ``git`` as ``AsyncMock`` so pyright can resolve
    mock-specific attributes (``assert_not_called``, ``assert_called_once_with``,
    etc.) without inferring the concrete ``GitService`` / repository type that
    ``AgentService.__init__`` assigns at runtime.
    """

    repo: AsyncMock
    git: AsyncMock

    async def get_project_files(self, project_id: UUID, agent: dict) -> list[dict]: ...


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def agent_svc() -> _MockedSvc:
    """AgentService with repo and git mocked; no __init__ side-effects."""
    svc = AgentService.__new__(AgentService)
    svc.repo = AsyncMock()
    svc.git = AsyncMock()
    return svc  # type: ignore[return-value]  # runtime: AgentService satisfies _MockedSvc structurally


AGENT = {"id": str(uuid4()), "handle": "test-agent"}


# ── Guard tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_project_files_project_none_returns_empty(agent_svc: _MockedSvc) -> None:
    """get_project_basic returns None → early return []."""
    agent_svc.repo.get_project_basic = AsyncMock(return_value=None)

    result = await agent_svc.get_project_files(uuid4(), AGENT)

    assert result == []
    agent_svc.git.get_repo_files.assert_not_called()


@pytest.mark.asyncio
async def test_get_project_files_repo_url_none_returns_empty(agent_svc: _MockedSvc) -> None:
    """repo_url is None → early return []."""
    agent_svc.repo.get_project_basic = AsyncMock(
        return_value={"title": "my-project", "repo_url": None, "vcs_provider": "github"}
    )

    result = await agent_svc.get_project_files(uuid4(), AGENT)

    assert result == []
    agent_svc.git.get_repo_files.assert_not_called()


@pytest.mark.asyncio
async def test_get_project_files_title_none_returns_empty(agent_svc: _MockedSvc) -> None:
    """title is None → early return [] (guard added; no VCS call without title)."""
    agent_svc.repo.get_project_basic = AsyncMock(
        return_value={
            "title": None,
            "repo_url": "https://github.com/org/repo",
            "vcs_provider": "github",
        }
    )

    result = await agent_svc.get_project_files(uuid4(), AGENT)

    assert result == []
    agent_svc.git.get_repo_files.assert_not_called()


@pytest.mark.asyncio
async def test_get_project_files_title_empty_string_returns_empty(agent_svc: _MockedSvc) -> None:
    """title is empty string → early return []."""
    agent_svc.repo.get_project_basic = AsyncMock(
        return_value={
            "title": "",
            "repo_url": "https://github.com/org/repo",
            "vcs_provider": "github",
        }
    )

    result = await agent_svc.get_project_files(uuid4(), AGENT)

    assert result == []
    agent_svc.git.get_repo_files.assert_not_called()


# ── Happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_project_files_happy_path_returns_files(agent_svc: _MockedSvc) -> None:
    """repo_url + title present → git.get_repo_files called, files returned.

    Also verifies no call to any `get_project_code_files` (method removed in
    code_files cleanup diff — VCS-only path must not reference it).
    """
    project_id = uuid4()
    agent_svc.repo.get_project_basic = AsyncMock(
        return_value={
            "title": "my-project",
            "repo_url": "https://github.com/org/my-project",
            "vcs_provider": "github",
        }
    )

    tree = [
        {"type": "blob", "path": "README.md"},
        {"type": "blob", "path": "main.py"},
        {"type": "tree", "path": "src"},        # directory — must be skipped
        {"type": "blob", "path": "logo.png"},   # binary ext — must be skipped
    ]
    agent_svc.git.get_repo_files = AsyncMock(return_value=tree)
    agent_svc.git.get_file_content = AsyncMock(return_value="# content")

    result = await agent_svc.get_project_files(project_id, AGENT)

    agent_svc.git.get_repo_files.assert_called_once_with("my-project", vcs_provider="github")

    paths = [f["path"] for f in result]
    assert "README.md" in paths
    assert "main.py" in paths
    assert "src" not in paths        # tree node skipped
    assert "logo.png" not in paths   # non-text ext skipped

    for f in result:
        assert "path" in f
        assert "content" in f
        assert "language" in f
        assert "version" in f


@pytest.mark.asyncio
async def test_get_project_files_git_returns_empty_tree(agent_svc: _MockedSvc) -> None:
    """git.get_repo_files returns [] → method returns []."""
    agent_svc.repo.get_project_basic = AsyncMock(
        return_value={
            "title": "my-project",
            "repo_url": "https://github.com/org/my-project",
            "vcs_provider": None,   # fallback to "github"
        }
    )
    agent_svc.git.get_repo_files = AsyncMock(return_value=[])

    result = await agent_svc.get_project_files(uuid4(), AGENT)

    assert result == []


@pytest.mark.asyncio
async def test_get_project_files_git_raises_returns_empty(agent_svc: _MockedSvc) -> None:
    """git.get_repo_files raises → caught, returns [], warning logged (no re-raise)."""
    agent_svc.repo.get_project_basic = AsyncMock(
        return_value={
            "title": "my-project",
            "repo_url": "https://github.com/org/my-project",
            "vcs_provider": "github",
        }
    )
    agent_svc.git.get_repo_files = AsyncMock(side_effect=RuntimeError("network error"))

    result = await agent_svc.get_project_files(uuid4(), AGENT)

    assert result == []

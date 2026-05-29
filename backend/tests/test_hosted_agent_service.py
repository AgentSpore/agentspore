"""Unit tests for HostedAgentService — quota enforcement and create flow.

No real DB or runner required; all external deps are mocked.
"""

from __future__ import annotations

from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.services.hosted_agent_service import HostedAgentService

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def create_svc_factory():
    """Factory fixture: returns a function that builds a HostedAgentService for testing.

    Default runner_url is empty (no runner). Pass runner_url to enable runner
    integration tests.
    """
    def _create(
        existing_agent_count: int = 0,
        max_hosted: int = 1,
        runner_url: str = "",
    ) -> HostedAgentService:
        repo = AsyncMock()
        repo.count_by_owner = AsyncMock(return_value=existing_agent_count)
        repo.create = AsyncMock(return_value={
            "id": "new-hosted-id",
            "agent_id": "new-agent-id",
            "owner_user_id": "u1",
            "status": "stopped",
            "model": "test/model:free",
            "system_prompt": "hello",
            "runtime": "python-minimal",
            "memory_limit_mb": 256,
        })
        repo.delete = AsyncMock()
        repo.db = AsyncMock()

        agent_svc = AsyncMock()
        agent_svc.db = AsyncMock()
        agent_svc.register_agent = AsyncMock(return_value={
            "agent_id": "new-agent-id",
            "api_key": "test-api-key",
            "handle": "test-handle",
        })

        openrouter = AsyncMock()
        openrouter.is_allowed = AsyncMock(return_value=True)

        openviking = AsyncMock()
        openviking.enabled = False

        settings = MagicMock()
        settings.max_hosted_agents_per_user = max_hosted
        settings.agent_runner_url = runner_url
        settings.agent_runner_key = ""
        settings.oauth_redirect_base_url = "https://agentspore.com"

        svc = HostedAgentService.__new__(HostedAgentService)
        svc.repo = repo
        svc.agent_svc = agent_svc
        svc.openrouter = openrouter
        svc.openviking = openviking
        svc.runner_url = runner_url
        svc.settings = settings
        svc._starting_locks = OrderedDict()
        return svc

    return _create


def _mock_runner_import_ok() -> MagicMock:
    """Return a mock httpx response for a successful runner import (status 200)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"imported": 3}
    resp.request = MagicMock()
    return resp


def _mock_runner_import_fail(status: int = 503) -> MagicMock:
    """Return a mock httpx response for a failed runner import.

    raise_for_status() raises httpx.HTTPStatusError, matching real httpx behaviour
    after switching from a manual status check to resp.raise_for_status().
    """
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {}
    resp.request = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"runner import returned {status}",
        request=resp.request,
        response=resp,
    )
    return resp


# ── Quota enforcement tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_hosted_agent_enforces_quota_at_limit(create_svc_factory):
    """create_hosted_agent raises HTTP 409 when user already owns max_hosted agents.

    This tests backend enforcement, not just the frontend button-hide.
    max_hosted_agents_per_user defaults to 1 (free tier).
    """
    svc = create_svc_factory(existing_agent_count=1, max_hosted=1)

    with pytest.raises(HTTPException) as exc_info:
        await svc.create_hosted_agent(
            user_id="u1",
            user_email="user@example.com",
            name="My Second Agent",
            system_prompt="Do stuff",
        )

    assert exc_info.value.status_code == 409
    assert "1" in exc_info.value.detail  # mentions the limit
    # Crucially: agent_svc.register_agent was NOT called (quota check fires first)
    svc.agent_svc.register_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_hosted_agent_enforces_quota_above_limit(create_svc_factory):
    """Quota fires even when user somehow has more agents than limit (data inconsistency guard)."""
    svc = create_svc_factory(existing_agent_count=3, max_hosted=1)

    with pytest.raises(HTTPException) as exc_info:
        await svc.create_hosted_agent(
            user_id="u1",
            user_email="user@example.com",
            name="Overflow Agent",
            system_prompt="Do stuff",
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_create_hosted_agent_succeeds_when_under_quota(create_svc_factory):
    """create_hosted_agent succeeds when user has 0 agents, limit is 1, runner is up."""
    svc = create_svc_factory(existing_agent_count=0, max_hosted=1, runner_url="http://runner:8080")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=_mock_runner_import_ok())
        mock_client_cls.return_value = mock_client

        result = await svc.create_hosted_agent(
            user_id="u1",
            user_email="user@example.com",
            name="My First Agent",
            system_prompt="Be helpful",
        )

    assert result["agent_name"] == "My First Agent"
    svc.agent_svc.register_agent.assert_awaited_once()
    svc.repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_hosted_agent_quota_uses_count_by_owner(create_svc_factory):
    """Quota is checked via repo.count_by_owner, not a frontend-only gate."""
    svc = create_svc_factory(existing_agent_count=0, max_hosted=1, runner_url="http://runner:8080")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=_mock_runner_import_ok())
        mock_client_cls.return_value = mock_client

        await svc.create_hosted_agent(
            user_id="u1",
            user_email="user@example.com",
            name="Test",
            system_prompt="Be helpful",
        )

    # count_by_owner must be called with the correct user_id
    svc.repo.count_by_owner.assert_awaited_once_with("u1")


@pytest.mark.asyncio
async def test_create_hosted_agent_multi_tenant_quota(create_svc_factory):
    """Two different users each under the limit can both create agents."""
    svc_u1 = create_svc_factory(existing_agent_count=0, max_hosted=1, runner_url="http://runner:8080")
    svc_u2 = create_svc_factory(existing_agent_count=0, max_hosted=1, runner_url="http://runner:8080")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=_mock_runner_import_ok())
        mock_client_cls.return_value = mock_client

        r1 = await svc_u1.create_hosted_agent(
            user_id="u1", user_email="u1@x.com", name="U1 Agent", system_prompt="go"
        )
        r2 = await svc_u2.create_hosted_agent(
            user_id="u2", user_email="u2@x.com", name="U2 Agent", system_prompt="go"
        )

    assert r1["agent_name"] == "U1 Agent"
    assert r2["agent_name"] == "U2 Agent"


@pytest.mark.asyncio
async def test_create_hosted_agent_model_not_available_rejected(create_svc_factory):
    """create_hosted_agent raises HTTP 400 if requested model is not on allowlist."""
    svc = create_svc_factory(existing_agent_count=0)
    svc.openrouter.is_allowed = AsyncMock(return_value=False)

    with pytest.raises(HTTPException) as exc_info:
        await svc.create_hosted_agent(
            user_id="u1",
            user_email="u@x.com",
            name="Agent",
            system_prompt="go",
            model="some/deprecated:model",
        )

    assert exc_info.value.status_code == 400
    svc.agent_svc.register_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_fork_hosted_agent_enforces_quota():
    """fork_hosted_agent also enforces the per-user quota (backend check)."""
    repo = AsyncMock()
    repo.count_by_owner = AsyncMock(return_value=1)  # already at limit
    repo.get_public_by_id = AsyncMock(return_value={
        "id": "source-id",
        "agent_id": "source-agent-id",
        "owner_user_id": "other-user",
        "agent_name": "Source Agent",
        "agent_handle": "source",
        "system_prompt": "do stuff",
        "model": "test/model:free",
        "specialization": "programmer",
        "skills": [],
        "description": "",
    })
    repo.db = AsyncMock()

    openrouter = AsyncMock()
    openrouter.is_allowed = AsyncMock(return_value=True)

    settings = MagicMock()
    settings.max_hosted_agents_per_user = 1

    svc = HostedAgentService.__new__(HostedAgentService)
    svc.repo = repo
    svc.openrouter = openrouter
    svc.settings = settings
    svc._starting_locks = OrderedDict()

    with pytest.raises(HTTPException) as exc_info:
        await svc.fork_hosted_agent(
            source_hosted_id="source-id",
            user_id="u2",
            user_email="u2@x.com",
        )

    assert exc_info.value.status_code == 409


# ── P4c: creation seeds runner dir, not DB ────────────────────────────────────


@pytest.mark.asyncio
async def test_create_seeds_runner_not_db(create_svc_factory):
    """P4c: creation calls runner import endpoint, NOT repo.upsert_file."""
    svc = create_svc_factory(runner_url="http://runner:8080")
    captured_payload: list[dict] = []

    async def _fake_post(url, *, json=None, headers=None, **_kw):
        if json:
            captured_payload.extend(json.get("files", []))
        return _mock_runner_import_ok()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_fake_post)
        mock_client_cls.return_value = mock_client

        await svc.create_hosted_agent(
            user_id="u1",
            user_email="u@x.com",
            name="My Agent",
            system_prompt="system prompt text",
        )

    # No DB writes for files
    svc.repo.upsert_file.assert_not_awaited()

    # Runner import was called
    mock_client.post.assert_awaited_once()
    call_url = mock_client.post.call_args[0][0]
    assert "files/import" in call_url
    assert "new-hosted-id" in call_url

    paths = {f["file_path"] for f in captured_payload}
    assert "AGENT.md" in paths
    assert "agent.yaml" in paths

    agent_md = next(f for f in captured_payload if f["file_path"] == "AGENT.md")
    assert agent_md["content"] == "system prompt text"


@pytest.mark.asyncio
async def test_create_seeds_skill_md_when_present(create_svc_factory):
    """P4c: .deep/skills/SKILL.md included in import when platform skill.md exists."""
    svc = create_svc_factory(runner_url="http://runner:8080")
    captured: list[dict] = []

    async def _fake_post(url, *, json=None, headers=None, **_kw):
        if json:
            captured.extend(json.get("files", []))
        return _mock_runner_import_ok()

    with (
        patch("httpx.AsyncClient") as mock_client_cls,
        patch(
            "app.services.hosted_agent_service._load_skill_md",
            return_value="# Platform Skill\nDo things.",
        ),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_fake_post)
        mock_client_cls.return_value = mock_client

        await svc.create_hosted_agent(
            user_id="u1",
            user_email="u@x.com",
            name="Agent",
            system_prompt="sp",
        )

    paths = {f["file_path"] for f in captured}
    assert ".deep/skills/SKILL.md" in paths
    skill_file = next(f for f in captured if f["file_path"] == ".deep/skills/SKILL.md")
    assert "Platform Skill" in skill_file["content"]


@pytest.mark.asyncio
async def test_create_seeds_custom_md_when_skills_provided(create_svc_factory):
    """P4c: .deep/skills/custom.md included when skills list is non-empty.

    Verifies custom.md is now on-disk from creation (no DB column required).
    """
    svc = create_svc_factory(runner_url="http://runner:8080")
    captured: list[dict] = []

    async def _fake_post(url, *, json=None, headers=None, **_kw):
        if json:
            captured.extend(json.get("files", []))
        return _mock_runner_import_ok()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_fake_post)
        mock_client_cls.return_value = mock_client

        await svc.create_hosted_agent(
            user_id="u1",
            user_email="u@x.com",
            name="Agent",
            system_prompt="sp",
            skills=["python", "testing"],
        )

    paths = {f["file_path"] for f in captured}
    assert ".deep/skills/custom.md" in paths

    custom = next(f for f in captured if f["file_path"] == ".deep/skills/custom.md")
    assert "python" in custom["content"]
    assert "testing" in custom["content"]


@pytest.mark.asyncio
async def test_create_no_custom_md_when_no_skills(create_svc_factory):
    """P4c: .deep/skills/custom.md NOT included when skills list is empty/absent."""
    svc = create_svc_factory(runner_url="http://runner:8080")
    captured: list[dict] = []

    async def _fake_post(url, *, json=None, headers=None, **_kw):
        if json:
            captured.extend(json.get("files", []))
        return _mock_runner_import_ok()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_fake_post)
        mock_client_cls.return_value = mock_client

        await svc.create_hosted_agent(
            user_id="u1",
            user_email="u@x.com",
            name="Agent",
            system_prompt="sp",
        )

    paths = {f["file_path"] for f in captured}
    assert ".deep/skills/custom.md" not in paths


@pytest.mark.asyncio
async def test_create_runner_down_raises_503_and_rollback(create_svc_factory):
    """P4c: runner unavailable at creation → 503, hosted row deleted, agent deactivated."""
    svc = create_svc_factory(runner_url="http://runner:8080")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(HTTPException) as exc_info:
            await svc.create_hosted_agent(
                user_id="u1",
                user_email="u@x.com",
                name="Agent",
                system_prompt="sp",
            )

    assert exc_info.value.status_code == 503
    assert "runner" in exc_info.value.detail.lower()

    # Row must be cleaned up
    svc.repo.delete.assert_awaited_once_with("new-hosted-id")
    # Platform agent deactivated
    svc.agent_svc.db.execute.assert_awaited()


@pytest.mark.asyncio
async def test_create_runner_error_status_raises_503_and_rollback(create_svc_factory):
    """P4c: runner returns non-200 → 503, hosted row deleted."""
    svc = create_svc_factory(runner_url="http://runner:8080")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=_mock_runner_import_fail(503))
        mock_client_cls.return_value = mock_client

        with pytest.raises(HTTPException) as exc_info:
            await svc.create_hosted_agent(
                user_id="u1",
                user_email="u@x.com",
                name="Agent",
                system_prompt="sp",
            )

    assert exc_info.value.status_code == 503
    svc.repo.delete.assert_awaited_once_with("new-hosted-id")


@pytest.mark.asyncio
async def test_create_no_runner_url_raises_503_and_rollback(create_svc_factory):
    """P4c: runner_url not configured → 503 immediately, no DB file writes."""
    svc = create_svc_factory(runner_url="")  # no runner

    with pytest.raises(HTTPException) as exc_info:
        await svc.create_hosted_agent(
            user_id="u1",
            user_email="u@x.com",
            name="Agent",
            system_prompt="sp",
        )

    assert exc_info.value.status_code == 503
    svc.repo.upsert_file.assert_not_awaited()
    svc.repo.delete.assert_awaited_once_with("new-hosted-id")


@pytest.mark.asyncio
async def test_create_rollback_delete_failure_still_raises_503(create_svc_factory):
    """P4c rollback safety: if repo.delete raises during rollback, the original 503
    is still raised (not swallowed or replaced by the delete error).

    Also verifies that the rollback delete failure is logged at ERROR level.
    """
    svc = create_svc_factory(runner_url="http://runner:8080")
    # Make the runner call fail (RequestError triggers rollback path)
    # AND make the rollback delete itself also fail
    svc.repo.delete = AsyncMock(side_effect=RuntimeError("DB gone"))

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client_cls.return_value = mock_client

        with patch("app.services.hosted_agent_service.logger") as mock_logger:
            with pytest.raises(HTTPException) as exc_info:
                await svc.create_hosted_agent(
                    user_id="u1",
                    user_email="u@x.com",
                    name="Agent",
                    system_prompt="sp",
                )

    # (a) Original 503 must propagate — not the delete RuntimeError
    assert exc_info.value.status_code == 503
    assert "runner" in exc_info.value.detail.lower()

    # (b) Rollback delete failure must be logged at ERROR level
    error_calls = [str(call) for call in mock_logger.error.call_args_list]
    assert any("orphaned" in msg or "Rollback delete failed" in msg for msg in error_calls), (
        f"Expected rollback-delete error log not found in: {error_calls}"
    )


@pytest.mark.asyncio
async def test_default_agent_yaml_single_source():
    """P4c: _default_agent_yaml() returns a non-empty string with expected keys."""
    yaml_content = HostedAgentService._default_agent_yaml()
    assert "include_todo" in yaml_content
    assert "include_filesystem" in yaml_content
    assert "skill_directories" in yaml_content
    assert "/workspace/.deep/skills" in yaml_content
    # Deterministic — calling twice returns identical content
    assert yaml_content == HostedAgentService._default_agent_yaml()

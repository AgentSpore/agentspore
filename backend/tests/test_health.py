"""Smoke tests for /health endpoint.

Guards against regressions like v1.26.4 -> v1.26.5 where a refactor
removed async_session_maker/get_redis/text imports from main.py while
the /health handler still used them directly. The bug surfaced only
at request time (NameError), not at import time, and there was no
coverage to catch it.

Calling the handler coroutine directly avoids lifespan/Redis setup.
Patching module-level symbols as strings forces the test to fail if
those symbols are missing from app.main namespace — which is exactly
the breakage we need to detect.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import app.main as main_module


class TestHealthEndpoint:
    """`/health` must stay wired to async_session_maker + get_redis."""

    def test_health_handler_imports_resolve(self):
        """app.main must expose the symbols /health depends on.

        If a refactor drops these imports again, this assertion fails
        before any HTTP machinery runs.
        """
        import app.main as main_module
        assert hasattr(main_module, "async_session_maker"), (
            "async_session_maker missing from app.main — /health will 503 at request time"
        )
        assert hasattr(main_module, "get_redis"), (
            "get_redis missing from app.main — /health will 503 at request time"
        )
        assert hasattr(main_module, "text"), (
            "sqlalchemy.text missing from app.main — /health will 503 at request time"
        )
        # /skill.md, /heartbeat.md, /rules.md handlers all call asyncio.to_thread
        # via _read_doc_file. v1.27.0 -> v1.27.1 hotfix: refactor dropped this
        # import along with the inline background-task loops.
        assert hasattr(main_module, "asyncio"), (
            "asyncio missing from app.main — /skill.md etc. will 500 at request time"
        )

    @pytest.mark.asyncio
    async def test_health_returns_healthy_when_deps_ok(self):
        """With DB and Redis mocked healthy, handler returns healthy JSON + 200."""
        from app.main import health

        fake_session = MagicMock()
        fake_session.execute = AsyncMock(return_value=None)
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=fake_session)
        session_cm.__aexit__ = AsyncMock(return_value=None)

        fake_redis = MagicMock()
        fake_redis.ping = AsyncMock(return_value=True)

        with (
            patch("app.main.async_session_maker", return_value=session_cm),
            patch("app.main.get_redis", AsyncMock(return_value=fake_redis)),
        ):
            response = await health()

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["status"] == "healthy"
        assert body["db"] == "ok"
        assert body["redis"] == "ok"

    @pytest.mark.asyncio
    async def test_health_returns_unhealthy_when_db_fails(self):
        """DB failure must produce status=unhealthy + 503."""
        from app.main import health

        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("db down"))
        session_cm.__aexit__ = AsyncMock(return_value=None)

        fake_redis = MagicMock()
        fake_redis.ping = AsyncMock(return_value=True)

        with (
            patch("app.main.async_session_maker", return_value=session_cm),
            patch("app.main.get_redis", AsyncMock(return_value=fake_redis)),
        ):
            response = await health()

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["status"] == "unhealthy"
        assert "error" in body["db"]


@pytest.mark.asyncio
async def test_skill_onboarding_uses_app_proxy_without_personal_oauth() -> None:
    """A1: serve the primary App/proxy path with personal OAuth optional."""
    skill_path = Path(__file__).resolve().parents[2] / "skill.md"
    with patch.object(main_module, "_find_doc_file", return_value=skill_path):
        async with AsyncClient(
            transport=ASGITransport(app=main_module.app), base_url="http://test"
        ) as client:
            response = await client.get("/skill.md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == skill_path.read_text(encoding="utf-8")
    onboarding = response.text.split("## Quick Start", 1)[1].split(
        "### Step 7:", 1
    )[0]
    assert "Step 2: GitHub Access (OAuth Optional)" in onboarding
    assert "configured GitHub App installation" in onboarding
    assert "repository permissions" in onboarding
    assert "without personal GitHub OAuth" in onboarding
    assert "Option A -- Push via GitHub proxy (recommended, no OAuth needed)" in onboarding
    assert "Option B -- Direct push (optional)" in onboarding
    assert onboarding.index('"method": "PUT"') < onboarding.index("/git-token")
    assert "requires GitHub OAuth" not in onboarding
    assert "GitHub OAuth is required" not in onboarding
    assert "Without it you can only read data and use chat" not in onboarding
    proxy = response.text.split("### GitHub API Proxy", 1)[1].split("```bash", 1)[0]
    assert "File writes always use a scoped App installation token" in proxy
    assert "503" in proxy
    assert "PAT" not in proxy
    assert "anonymous caller" not in proxy

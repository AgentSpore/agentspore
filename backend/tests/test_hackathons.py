"""Unit tests for hackathon auto-start gating."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from httpx import AsyncClient, ASGITransport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_result(return_value=None):
    """Return a mock that simulates db.execute() → .mappings().first()."""
    result = MagicMock()
    result.mappings.return_value.first.return_value = return_value
    result.scalar_one.return_value = 0
    return result


def _hackathon_row(hackathon_id, *, status="upcoming", min_projects=None, duration_days=None):
    return {
        "id": hackathon_id,
        "title": "Test Hackathon",
        "theme": "Testing",
        "description": "",
        "starts_at": "2099-01-01T00:00:00",
        "ends_at": "2099-02-01T00:00:00",
        "voting_ends_at": "2099-02-01T00:00:00",
        "status": status,
        "winner_project_id": None,
        "prize_pool_usd": 0,
        "prize_description": "",
        "created_at": "2099-01-01T00:00:00",
        "min_projects_to_start": min_projects,
        "duration_days": duration_days,
    }


# ---------------------------------------------------------------------------
# Unit tests — auto_start_if_threshold
# ---------------------------------------------------------------------------

class TestAutoStartIfThreshold:
    """Tests for hackathon_repo.auto_start_if_threshold."""

    @pytest.mark.asyncio
    async def test_returns_true_when_update_matches(self):
        """Should return True when the UPDATE ... RETURNING returns a row."""
        from app.repositories import hackathon_repo

        hackathon_id = uuid4()
        db = AsyncMock()
        result = MagicMock()
        result.mappings.return_value.first.return_value = {"id": hackathon_id}
        db.execute.return_value = result

        flipped = await hackathon_repo.auto_start_if_threshold(db, hackathon_id)
        assert flipped is True

    @pytest.mark.asyncio
    async def test_returns_false_when_update_matches_nothing(self):
        """Should return False when no row is updated (threshold not reached or wrong status)."""
        from app.repositories import hackathon_repo

        hackathon_id = uuid4()
        db = AsyncMock()
        result = MagicMock()
        result.mappings.return_value.first.return_value = None
        db.execute.return_value = result

        flipped = await hackathon_repo.auto_start_if_threshold(db, hackathon_id)
        assert flipped is False

    @pytest.mark.asyncio
    async def test_execute_called_with_correct_id(self):
        """Verify the UPDATE is executed with the correct hackathon_id binding."""
        from app.repositories import hackathon_repo

        hackathon_id = uuid4()
        db = AsyncMock()
        result = MagicMock()
        result.mappings.return_value.first.return_value = None
        db.execute.return_value = result

        await hackathon_repo.auto_start_if_threshold(db, hackathon_id)

        db.execute.assert_called_once()
        call_kwargs = db.execute.call_args
        # Second positional arg is the params dict
        params = call_kwargs[0][1]
        assert params["id"] == hackathon_id


# ---------------------------------------------------------------------------
# Unit tests — register-project endpoint triggers auto-start
# ---------------------------------------------------------------------------

class TestRegisterProjectAutoStart:
    """Tests for the register-project endpoint, verifying auto-start integration."""

    def _make_app(self, mock_db, agent_id):
        from app.main import app
        from app.core.database import get_db
        from app.services.agent_service import get_agent_by_api_key

        async def override_get_db():
            yield mock_db

        async def override_agent():
            return {"id": agent_id, "name": "TestAgent"}

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_agent_by_api_key] = override_agent
        return app

    @pytest.mark.asyncio
    async def test_auto_start_called_after_register(self):
        """auto_start_if_threshold is called after successful project registration."""
        from app.main import app

        hackathon_id = uuid4()
        project_id = uuid4()
        agent_id = uuid4()

        hackathon_row = {"id": hackathon_id, "status": "upcoming"}
        project_row = {
            "id": project_id,
            "title": "My Project",
            "creator_agent_id": agent_id,
            "hackathon_id": None,
            "team_id": None,
        }

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()

        with (
            patch("app.repositories.hackathon_repo.get_hackathon_status", return_value=hackathon_row),
            patch("app.repositories.hackathon_repo.get_project_for_registration", return_value=project_row),
            patch("app.repositories.hackathon_repo.register_project", return_value=None),
            patch("app.repositories.hackathon_repo.auto_start_if_threshold", new_callable=AsyncMock, return_value=False) as mock_auto,
        ):
            test_app = self._make_app(mock_db, agent_id)
            async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
                resp = await client.post(
                    f"/api/v1/hackathons/{hackathon_id}/register-project",
                    json={"project_id": str(project_id)},
                )

            mock_auto.assert_called_once()
            call_args = mock_auto.call_args[0]
            assert str(call_args[1]) == str(hackathon_id)
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_response_includes_hackathon_started_false(self):
        """Response body includes hackathon_started=False when threshold not reached."""
        from app.main import app

        hackathon_id = uuid4()
        project_id = uuid4()
        agent_id = uuid4()

        hackathon_row = {"id": hackathon_id, "status": "upcoming"}
        project_row = {
            "id": project_id,
            "title": "My Project",
            "creator_agent_id": agent_id,
            "hackathon_id": None,
            "team_id": None,
        }

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()

        with (
            patch("app.repositories.hackathon_repo.get_hackathon_status", return_value=hackathon_row),
            patch("app.repositories.hackathon_repo.get_project_for_registration", return_value=project_row),
            patch("app.repositories.hackathon_repo.register_project", return_value=None),
            patch("app.repositories.hackathon_repo.auto_start_if_threshold", new_callable=AsyncMock, return_value=False),
        ):
            test_app = self._make_app(mock_db, agent_id)
            async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
                resp = await client.post(
                    f"/api/v1/hackathons/{hackathon_id}/register-project",
                    json={"project_id": str(project_id)},
                )

            assert resp.status_code == 200
            body = resp.json()
            assert body["hackathon_started"] is False
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_response_includes_hackathon_started_true_on_flip(self):
        """Response body includes hackathon_started=True when threshold is reached."""
        from app.main import app

        hackathon_id = uuid4()
        project_id = uuid4()
        agent_id = uuid4()

        hackathon_row = {"id": hackathon_id, "status": "upcoming"}
        project_row = {
            "id": project_id,
            "title": "My Project",
            "creator_agent_id": agent_id,
            "hackathon_id": None,
            "team_id": None,
        }

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()

        with (
            patch("app.repositories.hackathon_repo.get_hackathon_status", return_value=hackathon_row),
            patch("app.repositories.hackathon_repo.get_project_for_registration", return_value=project_row),
            patch("app.repositories.hackathon_repo.register_project", return_value=None),
            patch("app.repositories.hackathon_repo.auto_start_if_threshold", new_callable=AsyncMock, return_value=True),
        ):
            test_app = self._make_app(mock_db, agent_id)
            async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
                resp = await client.post(
                    f"/api/v1/hackathons/{hackathon_id}/register-project",
                    json={"project_id": str(project_id)},
                )

            assert resp.status_code == 200
            body = resp.json()
            assert body["hackathon_started"] is True
            # db.commit should have been called twice: once after register_project, once after flip
            assert mock_db.commit.call_count == 2
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Unit tests — schemas
# ---------------------------------------------------------------------------

class TestHackathonSchemas:
    """Tests for new schema fields."""

    def test_create_request_accepts_min_projects_and_duration(self):
        from app.schemas.hackathons import HackathonCreateRequest
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        req = HackathonCreateRequest(
            title="Test Hackathon",
            theme="Testing",
            starts_at=now,
            ends_at=now,
            voting_ends_at=now,
            min_projects_to_start=5,
            duration_days=30,
        )
        assert req.min_projects_to_start == 5
        assert req.duration_days == 30

    def test_create_request_defaults_to_none(self):
        from app.schemas.hackathons import HackathonCreateRequest
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        req = HackathonCreateRequest(
            title="Test Hackathon",
            theme="Testing",
            starts_at=now,
            ends_at=now,
            voting_ends_at=now,
        )
        assert req.min_projects_to_start is None
        assert req.duration_days is None

    def test_response_includes_new_fields(self):
        from app.schemas.hackathons import HackathonResponse

        resp = HackathonResponse(
            id="abc",
            title="T",
            theme="X",
            description="",
            starts_at="2099-01-01",
            ends_at="2099-02-01",
            voting_ends_at="2099-02-01",
            status="upcoming",
            winner_project_id=None,
            prize_pool_usd=0.0,
            prize_description="",
            created_at="2099-01-01",
            min_projects_to_start=5,
            duration_days=30,
        )
        assert resp.min_projects_to_start == 5
        assert resp.duration_days == 30

    def test_response_new_fields_default_none(self):
        from app.schemas.hackathons import HackathonResponse

        resp = HackathonResponse(
            id="abc",
            title="T",
            theme="X",
            description="",
            starts_at="2099-01-01",
            ends_at="2099-02-01",
            voting_ends_at="2099-02-01",
            status="upcoming",
            winner_project_id=None,
            prize_pool_usd=0.0,
            prize_description="",
            created_at="2099-01-01",
        )
        assert resp.min_projects_to_start is None
        assert resp.duration_days is None

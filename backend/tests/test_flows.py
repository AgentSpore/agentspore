"""Tests for Agent Flows (DAG pipelines) feature."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport


def _setup_overrides(app, db, mock_redis=None):
    from app.core.database import get_db
    from app.core.redis_client import get_redis

    if mock_redis is None:
        mock_redis = AsyncMock()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: mock_redis


def _mock_user(user_id=None, name="TestUser", email="test@example.com"):
    from app.models import User
    user = MagicMock(spec=User)
    user.id = user_id or uuid.uuid4()
    user.name = name
    user.email = email
    user.is_admin = False
    return user


def _override_current_user(app, user):
    from app.api.deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: user


def _override_flow_deps(app, repo_mock=None, service_mock=None):
    from app.repositories.flow_repo import get_flow_repo
    from app.services.flow_service import get_flow_service
    if repo_mock:
        app.dependency_overrides[get_flow_repo] = lambda: repo_mock
    if service_mock:
        app.dependency_overrides[get_flow_service] = lambda: service_mock


# ==========================================
# Schema tests
# ==========================================

class TestFlowSchemas:
    """Validation tests for flow Pydantic schemas."""

    def test_create_flow_valid(self):
        from app.schemas.flows import CreateFlowRequest
        req = CreateFlowRequest(title="My Pipeline")
        assert req.title == "My Pipeline"
        assert req.description is None

    def test_create_flow_with_description(self):
        from app.schemas.flows import CreateFlowRequest
        req = CreateFlowRequest(title="Pipeline", description="Multi-agent task")
        assert req.description == "Multi-agent task"

    def test_create_flow_empty_title_rejected(self):
        from app.schemas.flows import CreateFlowRequest
        with pytest.raises(Exception):
            CreateFlowRequest(title="")

    def test_create_flow_title_too_long_rejected(self):
        from app.schemas.flows import CreateFlowRequest
        with pytest.raises(Exception):
            CreateFlowRequest(title="x" * 301)

    def test_update_flow_partial(self):
        from app.schemas.flows import UpdateFlowRequest
        req = UpdateFlowRequest(title="New Title")
        assert req.title == "New Title"
        assert req.description is None

    def test_add_step_valid(self):
        from app.schemas.flows import AddStepRequest
        req = AddStepRequest(agent_id="uuid-1", title="Research")
        assert req.agent_id == "uuid-1"
        assert req.depends_on == []
        assert req.auto_approve is False

    def test_add_step_with_depends(self):
        from app.schemas.flows import AddStepRequest
        req = AddStepRequest(agent_id="a", title="Step 2", depends_on=["step-1-id"])
        assert req.depends_on == ["step-1-id"]

    def test_add_step_empty_title_rejected(self):
        from app.schemas.flows import AddStepRequest
        with pytest.raises(Exception):
            AddStepRequest(agent_id="a", title="")

    def test_update_step_partial(self):
        from app.schemas.flows import UpdateStepRequest
        req = UpdateStepRequest(auto_approve=True)
        assert req.auto_approve is True
        assert req.agent_id is None

    def test_approve_step_with_edited_output(self):
        from app.schemas.flows import ApproveStepRequest
        req = ApproveStepRequest(edited_output="Fixed text")
        assert req.edited_output == "Fixed text"

    def test_approve_step_no_edit(self):
        from app.schemas.flows import ApproveStepRequest
        req = ApproveStepRequest()
        assert req.edited_output is None

    def test_reject_step_valid(self):
        from app.schemas.flows import RejectStepRequest
        req = RejectStepRequest(feedback="Please redo this part")
        assert req.feedback == "Please redo this part"

    def test_reject_step_empty_feedback_rejected(self):
        from app.schemas.flows import RejectStepRequest
        with pytest.raises(Exception):
            RejectStepRequest(feedback="")

    def test_skip_step_optional_reason(self):
        from app.schemas.flows import SkipStepRequest
        req = SkipStepRequest()
        assert req.reason is None
        req2 = SkipStepRequest(reason="Not needed")
        assert req2.reason == "Not needed"

    def test_step_message_valid(self):
        from app.schemas.flows import StepMessageRequest
        req = StepMessageRequest(content="Hello agent")
        assert req.message_type == "text"
        assert req.file_url is None

    def test_step_message_empty_rejected(self):
        from app.schemas.flows import StepMessageRequest
        with pytest.raises(Exception):
            StepMessageRequest(content="")

    def test_agent_complete_step_valid(self):
        from app.schemas.flows import AgentCompleteStepRequest
        req = AgentCompleteStepRequest(output_text="Done: here is the result")
        assert req.output_files == []

    def test_agent_complete_step_with_files(self):
        from app.schemas.flows import AgentCompleteStepRequest
        req = AgentCompleteStepRequest(
            output_text="Result",
            output_files=[{"url": "https://example.com/file.txt", "name": "file.txt"}],
        )
        assert len(req.output_files) == 1


# ==========================================
# DAG validation tests
# ==========================================

class TestDAGValidation:
    """Tests for FlowService DAG validation (Kahn's algorithm)."""

    def _service(self):
        from app.services.flow_service import FlowService
        return FlowService(repo=MagicMock())

    def test_valid_linear_dag(self):
        svc = self._service()
        steps = [
            {"id": "a", "title": "Step A", "depends_on": []},
            {"id": "b", "title": "Step B", "depends_on": ["a"]},
            {"id": "c", "title": "Step C", "depends_on": ["b"]},
        ]
        errors = svc.validate_dag(steps)
        assert errors == []

    def test_valid_parallel_dag(self):
        svc = self._service()
        steps = [
            {"id": "a", "title": "Research", "depends_on": []},
            {"id": "b", "title": "Design", "depends_on": []},
            {"id": "c", "title": "Merge", "depends_on": ["a", "b"]},
        ]
        errors = svc.validate_dag(steps)
        assert errors == []

    def test_cycle_detected(self):
        svc = self._service()
        steps = [
            {"id": "a", "title": "A", "depends_on": ["b"]},
            {"id": "b", "title": "B", "depends_on": ["a"]},
        ]
        errors = svc.validate_dag(steps)
        assert len(errors) == 1
        assert "cycle" in errors[0].lower()

    def test_self_loop_detected(self):
        svc = self._service()
        steps = [
            {"id": "a", "title": "A", "depends_on": ["a"]},
        ]
        errors = svc.validate_dag(steps)
        assert len(errors) == 1
        assert "cycle" in errors[0].lower()

    def test_dangling_reference(self):
        svc = self._service()
        steps = [
            {"id": "a", "title": "A", "depends_on": ["nonexistent"]},
        ]
        errors = svc.validate_dag(steps)
        assert len(errors) >= 1
        assert "unknown" in errors[0].lower()

    def test_single_node_valid(self):
        svc = self._service()
        steps = [{"id": "a", "title": "Only step", "depends_on": []}]
        errors = svc.validate_dag(steps)
        assert errors == []

    def test_complex_diamond_dag(self):
        svc = self._service()
        steps = [
            {"id": "a", "title": "A", "depends_on": []},
            {"id": "b", "title": "B", "depends_on": ["a"]},
            {"id": "c", "title": "C", "depends_on": ["a"]},
            {"id": "d", "title": "D", "depends_on": ["b", "c"]},
        ]
        errors = svc.validate_dag(steps)
        assert errors == []

    def test_three_node_cycle(self):
        svc = self._service()
        steps = [
            {"id": "a", "title": "A", "depends_on": ["c"]},
            {"id": "b", "title": "B", "depends_on": ["a"]},
            {"id": "c", "title": "C", "depends_on": ["b"]},
        ]
        errors = svc.validate_dag(steps)
        assert len(errors) == 1
        assert "cycle" in errors[0].lower()


# ==========================================
# Input assembly tests
# ==========================================

class TestInputAssembly:
    """Tests for FlowService._assemble_input."""

    def _service(self):
        from app.services.flow_service import FlowService
        return FlowService(repo=MagicMock())

    def test_no_deps_no_instructions(self):
        svc = self._service()
        step = {"instructions": None, "depends_on": []}
        result = svc._assemble_input(step, [])
        assert result == ""

    def test_instructions_only(self):
        svc = self._service()
        step = {"instructions": "Write a report", "depends_on": []}
        result = svc._assemble_input(step, [])
        assert "Write a report" in result

    def test_upstream_output_included(self):
        svc = self._service()
        all_steps = [
            {"id": "a", "title": "Research", "output_text": "Research findings here"},
        ]
        step = {"instructions": "Summarize", "depends_on": ["a"]}
        result = svc._assemble_input(step, all_steps)
        assert "Research findings here" in result
        assert "Summarize" in result

    def test_multiple_upstream_outputs(self):
        svc = self._service()
        all_steps = [
            {"id": "a", "title": "Step A", "output_text": "Output A"},
            {"id": "b", "title": "Step B", "output_text": "Output B"},
        ]
        step = {"instructions": None, "depends_on": ["a", "b"]}
        result = svc._assemble_input(step, all_steps)
        assert "Output A" in result
        assert "Output B" in result


# ==========================================
# Heartbeat integration
# ==========================================

class TestHeartbeatFlows:
    """Test that flow_steps field is present in heartbeat response."""

    def test_heartbeat_response_has_flow_steps_field(self):
        from app.schemas.agents import HeartbeatResponseBody
        resp = HeartbeatResponseBody()
        assert hasattr(resp, "flow_steps")
        assert resp.flow_steps == []

    def test_heartbeat_response_with_flow_steps(self):
        from app.schemas.agents import HeartbeatResponseBody
        resp = HeartbeatResponseBody(flow_steps=[
            {"step_id": "s-1", "flow_id": "f-1", "flow_title": "Pipeline", "title": "Research", "status": "ready"},
        ])
        assert len(resp.flow_steps) == 1
        assert resp.flow_steps[0]["status"] == "ready"


# ==========================================
# API auth tests
# ==========================================

class TestFlowAPIAuth:
    """Tests for flow API authentication requirements."""

    @pytest.mark.asyncio
    async def test_create_flow_no_auth_returns_401(self):
        from app.main import app
        db = AsyncMock()
        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/flows",
                    json={"title": "My Flow"},
                )
            assert response.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_flows_no_auth_returns_401(self):
        from app.main import app
        db = AsyncMock()
        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/flows")
            assert response.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_flow_no_auth_returns_401(self):
        from app.main import app
        db = AsyncMock()
        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/flows/{uuid.uuid4()}")
            assert response.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_agent_steps_no_key_returns_422(self):
        from app.main import app
        db = AsyncMock()
        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/flows/agent/my-steps")
            assert response.status_code == 422
        finally:
            app.dependency_overrides.clear()


# ==========================================
# API functional tests
# ==========================================

class TestFlowAPIFunctional:
    """Functional tests for flow API with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_create_flow_success(self):
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)

        user = _mock_user(name="Alice")
        _override_current_user(app, user)

        flow_id = str(uuid.uuid4())
        repo_mock = MagicMock()
        repo_mock.create_flow = AsyncMock(return_value={
            "id": flow_id, "status": "draft", "created_at": "2026-03-14T10:00:00",
        })

        _override_flow_deps(app, repo_mock=repo_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/flows",
                    json={"title": "My Pipeline"},
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["id"] == flow_id
            assert data["status"] == "draft"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_flows_empty(self):
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)

        user = _mock_user()
        _override_current_user(app, user)

        repo_mock = MagicMock()
        repo_mock.list_user_flows = AsyncMock(return_value=[])

        _override_flow_deps(app, repo_mock=repo_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/v1/flows",
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 200
            assert response.json() == []
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_flow_not_found(self):
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)

        user = _mock_user()
        _override_current_user(app, user)

        repo_mock = MagicMock()
        repo_mock.get_flow_by_id = AsyncMock(return_value=None)

        _override_flow_deps(app, repo_mock=repo_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/flows/{uuid.uuid4()}",
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_flow_access_denied(self):
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)

        user = _mock_user()
        _override_current_user(app, user)

        flow_id = str(uuid.uuid4())
        other_user = uuid.uuid4()

        repo_mock = MagicMock()
        repo_mock.get_flow_by_id = AsyncMock(return_value={
            "id": flow_id, "user_id": other_user, "title": "Other's flow",
            "description": None, "status": "draft",
            "total_price_tokens": 0, "total_platform_fee": 0,
            "created_at": "2026-03-14", "started_at": None,
            "completed_at": None, "cancelled_at": None,
            "user_name": "Other",
        })

        _override_flow_deps(app, repo_mock=repo_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/flows/{flow_id}",
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 403
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_delete_non_draft_flow_rejected(self):
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)

        user = _mock_user()
        _override_current_user(app, user)

        flow_id = str(uuid.uuid4())

        repo_mock = MagicMock()
        repo_mock.get_flow_by_id = AsyncMock(return_value={
            "id": flow_id, "user_id": user.id, "title": "Running flow",
            "description": None, "status": "running",
            "total_price_tokens": 0, "total_platform_fee": 0,
            "created_at": "2026-03-14", "started_at": "2026-03-14",
            "completed_at": None, "cancelled_at": None,
            "user_name": "TestUser",
        })

        _override_flow_deps(app, repo_mock=repo_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.delete(
                    f"/api/v1/flows/{flow_id}",
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 400
            assert "draft" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_start_flow_no_steps_rejected(self):
        from app.main import app

        db = AsyncMock()
        mock_redis = AsyncMock()
        _setup_overrides(app, db, mock_redis)

        user = _mock_user()
        _override_current_user(app, user)

        flow_id = str(uuid.uuid4())

        repo_mock = MagicMock()
        repo_mock.get_flow_by_id = AsyncMock(return_value={
            "id": flow_id, "user_id": user.id, "title": "Empty flow",
            "description": None, "status": "draft",
            "total_price_tokens": 0, "total_platform_fee": 0,
            "created_at": "2026-03-14", "started_at": None,
            "completed_at": None, "cancelled_at": None,
            "user_name": "TestUser",
        })
        repo_mock.get_flow_steps = AsyncMock(return_value=[])

        from app.services.flow_service import FlowService
        service_mock = FlowService(repo=repo_mock)

        _override_flow_deps(app, repo_mock=repo_mock, service_mock=service_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/flows/{flow_id}/start",
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 400
            assert "no steps" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_add_step_to_non_draft_rejected(self):
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)

        user = _mock_user()
        _override_current_user(app, user)

        flow_id = str(uuid.uuid4())

        repo_mock = MagicMock()
        repo_mock.get_flow_by_id = AsyncMock(return_value={
            "id": flow_id, "user_id": user.id, "title": "Running",
            "description": None, "status": "running",
            "total_price_tokens": 0, "total_platform_fee": 0,
            "created_at": "2026-03-14", "started_at": "2026-03-14",
            "completed_at": None, "cancelled_at": None,
            "user_name": "TestUser",
        })

        _override_flow_deps(app, repo_mock=repo_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/flows/{flow_id}/steps",
                    json={"agent_id": str(uuid.uuid4()), "title": "New step"},
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 400
            assert "draft" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_add_step_agent_not_found(self):
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)

        user = _mock_user()
        _override_current_user(app, user)

        flow_id = str(uuid.uuid4())

        repo_mock = MagicMock()
        repo_mock.get_flow_by_id = AsyncMock(return_value={
            "id": flow_id, "user_id": user.id, "title": "Draft",
            "description": None, "status": "draft",
            "total_price_tokens": 0, "total_platform_fee": 0,
            "created_at": "2026-03-14", "started_at": None,
            "completed_at": None, "cancelled_at": None,
            "user_name": "TestUser",
        })

        _override_flow_deps(app, repo_mock=repo_mock)

        try:
            with patch("app.repositories.agent_repo.get_agent_by_id", new_callable=AsyncMock, return_value=None):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/flows/{flow_id}/steps",
                        json={"agent_id": str(uuid.uuid4()), "title": "Research"},
                        headers={"Authorization": "Bearer fake-token"},
                    )

            assert response.status_code == 404
            assert "agent not found" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

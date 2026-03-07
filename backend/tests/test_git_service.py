"""
Unit-тесты для GitService — регрессия багов, найденных при интеграционном тестировании.

Баги:
  #1 — GitService не проксировал новые методы GitHubService:
       list_issues, comment_issue, list_pull_requests,
       list_commits, get_file_content.
       Все вызовы падали с AttributeError → HTTP 500.

  #2 — GitService.push_files не пробрасывал параметр branch в GitHubService.push_files.
       Коммит на feature-ветку игнорировал branch и писал в main.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_git_service_with_mock_github():
    """Создать GitService с замоканным GitHubService внутри."""
    from app.services.git_service import GitService

    svc = GitService()
    mock_gh = MagicMock()
    svc._github = mock_gh
    return svc, mock_gh


# ===========================================================================
# Bug #2 — branch parameter not forwarded in push_files
# ===========================================================================

class TestPushFilesBranchParameter:
    """Регрессия Bug #2: branch должен передаваться в GitHubService.push_files."""

    @pytest.mark.asyncio
    async def test_push_files_forwards_branch(self):
        """push_files(branch='feat/x') должен вызвать github.push_files с branch='feat/x'."""
        svc, mock_gh = make_git_service_with_mock_github()
        mock_gh.push_files = AsyncMock(return_value=True)

        await svc.push_files(
            repo_name="my-repo",
            files=[{"path": "test.py", "content": "pass", "language": "python"}],
            commit_message="test commit",
            branch="feat/integration-test",
        )

        mock_gh.push_files.assert_called_once()
        _, kwargs = mock_gh.push_files.call_args
        assert kwargs.get("branch") == "feat/integration-test", (
            "branch не был передан в GitHubService.push_files — Bug #2"
        )

    @pytest.mark.asyncio
    async def test_push_files_default_branch_is_main(self):
        """По умолчанию push_files использует branch='main'."""
        svc, mock_gh = make_git_service_with_mock_github()
        mock_gh.push_files = AsyncMock(return_value=True)

        await svc.push_files(
            repo_name="my-repo",
            files=[{"path": "a.py", "content": "x", "language": "python"}],
            commit_message="default branch test",
        )

        _, kwargs = mock_gh.push_files.call_args
        assert kwargs.get("branch") == "main"

    @pytest.mark.asyncio
    async def test_push_files_main_branch_not_affected_by_feature_push(self):
        """Два последовательных push_files — каждый использует свой branch."""
        svc, mock_gh = make_git_service_with_mock_github()
        mock_gh.push_files = AsyncMock(return_value=True)

        await svc.push_files("repo", [{"path": "a.py", "content": "1", "language": "python"}],
                              "feat commit", branch="feat/x")
        await svc.push_files("repo", [{"path": "b.py", "content": "2", "language": "python"}],
                              "main commit", branch="main")

        calls = mock_gh.push_files.call_args_list
        assert calls[0][1]["branch"] == "feat/x"
        assert calls[1][1]["branch"] == "main"


# ===========================================================================
# Bug #1 — GitService missing proxy methods
# ===========================================================================

class TestGitServiceProxyMethods:
    """
    Регрессия Bug #1: каждый новый метод GitHubService должен быть доступен
    через GitService. До исправления все они бросали AttributeError.
    """

    @pytest.mark.asyncio
    async def test_list_issues_exists_and_delegates(self):
        """GitService.list_issues делегирует в GitHubService.list_issues."""
        svc, mock_gh = make_git_service_with_mock_github()
        mock_gh.list_issues = AsyncMock(return_value=[
            {"number": 1, "title": "Bug found", "state": "open"}
        ])

        result = await svc.list_issues("my-repo", state="open")

        mock_gh.list_issues.assert_called_once_with("my-repo", "open")
        assert len(result) == 1
        assert result[0]["number"] == 1

    @pytest.mark.asyncio
    async def test_list_issues_state_all(self):
        """list_issues пробрасывает state='all'."""
        svc, mock_gh = make_git_service_with_mock_github()
        mock_gh.list_issues = AsyncMock(return_value=[])

        await svc.list_issues("repo", state="all")

        mock_gh.list_issues.assert_called_once_with("repo", "all")

    @pytest.mark.asyncio
    async def test_comment_issue_exists_and_delegates(self):
        """GitService.comment_issue делегирует в GitHubService."""
        svc, mock_gh = make_git_service_with_mock_github()
        expected = {"id": 42, "url": "https://github.com/.../issues/1#comment-42"}
        mock_gh.comment_issue = AsyncMock(return_value=expected)

        result = await svc.comment_issue("my-repo", 1, "Fixing in next commit.")

        mock_gh.comment_issue.assert_called_once_with("my-repo", 1, "Fixing in next commit.", user_token=None)
        assert result == expected

    @pytest.mark.asyncio
    async def test_list_pull_requests_exists_and_delegates(self):
        """GitService.list_pull_requests делегирует в GitHubService."""
        svc, mock_gh = make_git_service_with_mock_github()
        mock_gh.list_pull_requests = AsyncMock(return_value=[
            {"number": 2, "title": "feat: new feature", "state": "open"}
        ])

        result = await svc.list_pull_requests("my-repo", state="open")

        mock_gh.list_pull_requests.assert_called_once_with("my-repo", "open")
        assert result[0]["number"] == 2

    @pytest.mark.asyncio
    async def test_list_commits_exists_and_delegates(self):
        """GitService.list_commits пробрасывает branch и limit."""
        svc, mock_gh = make_git_service_with_mock_github()
        mock_gh.list_commits = AsyncMock(return_value=[
            {"sha": "abc1234", "message": "Initial commit", "author": "bot"}
        ])

        result = await svc.list_commits("my-repo", branch="feat/x", limit=5)

        mock_gh.list_commits.assert_called_once_with("my-repo", "feat/x", 5)
        assert result[0]["sha"] == "abc1234"

    @pytest.mark.asyncio
    async def test_list_commits_default_branch_and_limit(self):
        """list_commits по умолчанию: branch='main', limit=20."""
        svc, mock_gh = make_git_service_with_mock_github()
        mock_gh.list_commits = AsyncMock(return_value=[])

        await svc.list_commits("my-repo")

        mock_gh.list_commits.assert_called_once_with("my-repo", "main", 20)

    @pytest.mark.asyncio
    async def test_get_file_content_exists_and_delegates(self):
        """GitService.get_file_content пробрасывает file_path и branch."""
        svc, mock_gh = make_git_service_with_mock_github()
        mock_gh.get_file_content = AsyncMock(return_value="# Hello World")

        result = await svc.get_file_content("my-repo", "README.md", branch="feat/x")

        mock_gh.get_file_content.assert_called_once_with("my-repo", "README.md", "feat/x")
        assert result == "# Hello World"

    @pytest.mark.asyncio
    async def test_get_file_content_default_branch(self):
        """get_file_content по умолчанию читает из 'main'."""
        svc, mock_gh = make_git_service_with_mock_github()
        mock_gh.get_file_content = AsyncMock(return_value="content")

        await svc.get_file_content("my-repo", "src/main.py")

        mock_gh.get_file_content.assert_called_once_with("my-repo", "src/main.py", "main")

    def test_all_new_methods_present_on_git_service(self):
        """
        Smoke-тест: все новые методы существуют на GitService.
        До исправления Bug #1 этот тест падал бы с AttributeError.
        """
        from app.services.git_service import GitService

        svc = GitService()
        new_methods = [
            "list_issues", "comment_issue",
            "list_pull_requests",
            "list_commits", "get_file_content",
        ]
        for method in new_methods:
            assert hasattr(svc, method), (
                f"GitService.{method} отсутствует — Bug #1 не исправлен"
            )
            assert callable(getattr(svc, method)), (
                f"GitService.{method} не является callable"
            )


# ===========================================================================
# API endpoint regression tests (Issues / Commits / Branches via HTTP)
# ===========================================================================

class TestIssuesEndpointRegression:
    """
    Регрессия: до исправления Bug #1 GET /agents/projects/:id/issues
    возвращал HTTP 500 (AttributeError: 'GitService' object has no attribute 'list_issues').
    """

    @pytest.fixture
    def mock_agent(self):
        return {
            "id": "bf5492cb-2e54-46cb-bd37-c0cb8921b1de",
            "name": "TestAgent",
            "specialization": "reviewer",
            "karma": 100,
        }

    @pytest.fixture
    def mock_project(self):
        return {"title": "PreBuild Validator", "repo_url": "https://github.com/AgentSpore/prebuild-validator"}

    @pytest.mark.asyncio
    async def test_list_issues_returns_200_not_500(self, mock_agent, mock_project):
        """GET /projects/:id/issues → 200, не 500 (AttributeError регрессия)."""
        from app.main import app
        from app.core.database import get_db
        import app.api.v1.agents as agents_module

        db = AsyncMock()
        project_result = MagicMock()
        project_result.mappings.return_value.first.return_value = mock_project
        agent_result = MagicMock()
        agent_result.mappings.return_value.first.return_value = mock_agent
        db.execute.side_effect = [agent_result, project_result]

        async def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            with patch("app.api.v1.agents.get_git_service") as mock_git_factory:
                mock_git = MagicMock()
                mock_git.list_issues = AsyncMock(return_value=[
                    {"number": 1, "title": "Test issue", "state": "open",
                     "body": "", "labels": [], "created_at": "2026-01-01T00:00:00Z",
                     "url": "https://github.com/AgentSpore/repo/issues/1"}
                ])
                mock_git_factory.return_value = mock_git

                from httpx import AsyncClient, ASGITransport
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        "/api/v1/agents/projects/e5547196-6646-4c1a-ae3f-6c1f90a803d6/issues",
                        headers={"X-API-Key": "af_test_key"},
                    )

            assert response.status_code == 200, (
                f"Ожидали 200, получили {response.status_code}: {response.text[:200]}"
            )
            data = response.json()
            assert "issues" in data
            assert data["count"] == 1
            assert data["issues"][0]["number"] == 1
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_commits_endpoint_passes_branch_param(self, mock_agent, mock_project):
        """
        GET /projects/:id/commits?branch=feat/x должен вызвать
        git.list_commits с branch='feat/x', а не 'main' (регрессия Bug #2).
        """
        from app.main import app
        from app.core.database import get_db

        db = AsyncMock()
        project_result = MagicMock()
        project_result.mappings.return_value.first.return_value = mock_project
        agent_result = MagicMock()
        agent_result.mappings.return_value.first.return_value = mock_agent
        db.execute.side_effect = [agent_result, project_result]

        async def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            with patch("app.api.v1.agents.get_git_service") as mock_git_factory:
                mock_git = MagicMock()
                mock_git.list_commits = AsyncMock(return_value=[
                    {"sha": "abc1234", "message": "test commit",
                     "author": "bot", "date": "2026-01-01T00:00:00Z", "url": ""}
                ])
                mock_git_factory.return_value = mock_git

                from httpx import AsyncClient, ASGITransport
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        "/api/v1/agents/projects/e5547196-6646-4c1a-ae3f-6c1f90a803d6/commits",
                        params={"branch": "feat/integration-test", "limit": 5},
                        headers={"X-API-Key": "af_test_key"},
                    )

            assert response.status_code == 200
            mock_git.list_commits.assert_called_once_with(
                mock_project["title"], branch="feat/integration-test", limit=5
            )
        finally:
            app.dependency_overrides.clear()


class TestPublicProjectsEndpoints:
    """
    Тесты публичных эндпоинтов проектов (добавлены в этой сессии):
    GET /api/v1/projects и POST /api/v1/projects/:id/vote.
    """

    @pytest.mark.asyncio
    async def test_list_projects_no_auth_required(self):
        """GET /api/v1/projects — публичный, не требует API-Key."""
        from app.main import app
        from app.core.database import get_db

        db = AsyncMock()
        result = MagicMock()
        result.mappings.return_value = []
        db.execute.return_value = result

        async def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            from httpx import AsyncClient, ASGITransport
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/projects")

            assert response.status_code == 200
            assert isinstance(response.json(), list)
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_vote_upvote_valid(self):
        """POST /api/v1/projects/:id/vote с vote=1 → обновляет votes_up."""
        from app.main import app
        from app.core.database import get_db

        db = AsyncMock()

        async def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            with patch("app.api.v1.projects.project_repo") as mock_repo:
                mock_repo.project_exists = AsyncMock(return_value=True)
                mock_repo.count_votes_in_period = AsyncMock(return_value=0)
                mock_repo.get_last_vote_time = AsyncMock(return_value=None)
                mock_repo.get_previous_vote = AsyncMock(return_value=None)
                mock_repo.insert_vote = AsyncMock()
                mock_repo.get_vote_counts = AsyncMock(return_value={"votes_up": 5, "votes_down": 1})

                from httpx import AsyncClient, ASGITransport
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        "/api/v1/projects/e5547196-6646-4c1a-ae3f-6c1f90a803d6/vote",
                        json={"vote": 1},
                    )

            assert response.status_code == 200
            data = response.json()
            assert "votes_up" in data
            assert "votes_down" in data
            assert "score" in data
            assert data["score"] == 4
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_vote_invalid_value_returns_422(self):
        """POST /api/v1/projects/:id/vote с vote=0 (невалидно) → 422."""
        from app.main import app
        from app.core.database import get_db

        db = AsyncMock()
        async def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            from httpx import AsyncClient, ASGITransport
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/projects/e5547196-6646-4c1a-ae3f-6c1f90a803d6/vote",
                    json={"vote": 0},
                )

            assert response.status_code == 422
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_vote_invalid_value_2_returns_422(self):
        """POST /api/v1/projects/:id/vote с vote=2 (не ±1) → 422."""
        from app.main import app
        from app.core.database import get_db

        db = AsyncMock()
        async def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            from httpx import AsyncClient, ASGITransport
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/projects/e5547196-6646-4c1a-ae3f-6c1f90a803d6/vote",
                    json={"vote": 2},
                )

            assert response.status_code == 422
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_projects_status_filter(self):
        """GET /api/v1/projects?status=deployed — параметр принимается (не 422)."""
        from app.main import app
        from app.core.database import get_db

        db = AsyncMock()
        result = MagicMock()
        result.mappings.return_value = []
        db.execute.return_value = result

        async def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            from httpx import AsyncClient, ASGITransport
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/projects?status=deployed")

            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()


class TestActivityEndpointAgentId:
    """
    Регрессия: activity REST API не возвращал agent_id в событиях.
    Убеждаемся что поле присутствует в ответе.
    """

    @pytest.mark.asyncio
    async def test_activity_response_includes_agent_id(self):
        """GET /api/v1/activity → каждое событие содержит поле agent_id."""
        from app.main import app
        from app.core.database import get_db
        import uuid

        db = AsyncMock()
        result = MagicMock()
        agent_uuid = uuid.uuid4()
        result.mappings.return_value = [
            {
                "id": uuid.uuid4(),
                "agent_id": agent_uuid,
                "agent_name": "TestAgent",
                "specialization": "programmer",
                "action_type": "code_commit",
                "description": "Committed 3 file(s): feat: add export",
                "project_id": uuid.uuid4(),
                "metadata": None,
                "created_at": "2026-02-20T10:00:00+00:00",
            }
        ]
        db.execute.return_value = result

        async def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            from httpx import AsyncClient, ASGITransport
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/activity")

            assert response.status_code == 200
            events = response.json()
            assert len(events) == 1
            assert "agent_id" in events[0], (
                "agent_id отсутствует в ответе activity — ссылки /agents/undefined не будут работать"
            )
            assert events[0]["agent_id"] == str(agent_uuid)
        finally:
            app.dependency_overrides.clear()

"""Git Service — унифицированный интерфейс для GitHub и GitLab."""

import logging
from typing import Any

from app.services.github_service import GitHubService, get_github_service
from app.services.gitlab_service import GitLabService, get_gitlab_service

logger = logging.getLogger("git_service")


class GitService:
    """Интерфейс для Git операций — маршрутизирует к GitHub или GitLab."""

    def __init__(self):
        self._github: GitHubService | None = None
        self._gitlab: GitLabService | None = None

    @property
    def github(self) -> GitHubService:
        if self._github is None:
            self._github = get_github_service()
        return self._github

    @property
    def gitlab(self) -> GitLabService:
        if self._gitlab is None:
            self._gitlab = get_gitlab_service()
        return self._gitlab

    def _svc(self, vcs_provider: str) -> GitHubService | GitLabService:
        """Вернуть нужный сервис по провайдеру."""
        if vcs_provider == "gitlab":
            return self.gitlab
        return self.github

    async def initialize(self, vcs_provider: str = "github") -> bool:
        return await self._svc(vcs_provider).initialize()

    def create_agent_identity(
        self,
        agent_name: str,
        agent_email: str | None = None,
    ) -> dict[str, str]:
        """Создать Git identity для агента (одинаково для обоих провайдеров)."""
        identity = self.github.create_agent_identity(agent_name, agent_email)
        identity["token"] = ""
        return identity

    async def create_repo(
        self,
        repo_name: str,
        description: str = "",
        private: bool = False,
        user_token: str | None = None,
        vcs_provider: str = "github",
    ) -> str | None:
        return await self._svc(vcs_provider).create_repo(
            repo_name, description, private, user_token=user_token
        )

    async def setup_repo_admin(self, repo_name: str, vcs_provider: str = "github") -> None:
        """Branch protection через admin-токен (App token или PAT)."""
        await self._svc(vcs_provider).setup_repo_admin(repo_name)

    async def add_repo_collaborator(self, repo_name: str, username: str, permission: str = "push", vcs_provider: str = "github") -> bool:
        """Добавить пользователя как collaborator на репозиторий."""
        if vcs_provider == "github":
            return await self.github.add_repo_collaborator(repo_name, username, permission)
        # GitLab: TODO — add project member via gitlab_service
        return False

    async def invite_to_org(self, username: str, vcs_provider: str = "github") -> None:
        """Пригласить пользователя в org/group."""
        if vcs_provider == "gitlab":
            await self.gitlab.invite_to_group(username)
        else:
            await self.github.invite_to_org(username)

    async def push_files(
        self,
        repo_name: str,
        files: list[dict[str, str]],
        commit_message: str = "Auto-commit by agent",
        agent_token: str | None = None,
        agent_identity: dict[str, str] | None = None,
        branch: str = "main",
        user_token: str | None = None,
        vcs_provider: str = "github",
    ) -> bool:
        return await self._svc(vcs_provider).push_files(
            repo_name=repo_name,
            files=files,
            commit_message=commit_message,
            agent_identity=agent_identity,
            branch=branch,
            user_token=user_token,
        )

    async def get_repo_url(self, repo_name: str, vcs_provider: str = "github") -> str:
        return await self._svc(vcs_provider).get_repo_url(repo_name)

    async def get_repo_files(self, repo_name: str, vcs_provider: str = "github") -> list[dict] | None:
        return await self._svc(vcs_provider).get_repo_files(repo_name)

    async def create_issue(
        self,
        repo_name: str,
        title: str,
        body: str,
        labels: list[str] = [],
        user_token: str | None = None,
        vcs_provider: str = "github",
    ) -> dict | None:
        return await self._svc(vcs_provider).create_issue(
            repo_name, title, body, labels, user_token=user_token
        )

    async def list_issues(self, repo_name: str, state: str = "open", vcs_provider: str = "github") -> list[dict]:
        return await self._svc(vcs_provider).list_issues(repo_name, state)

    async def comment_issue(
        self, repo_name: str, issue_number: int, body: str,
        user_token: str | None = None, vcs_provider: str = "github",
    ) -> dict | None:
        return await self._svc(vcs_provider).comment_issue(
            repo_name, issue_number, body, user_token=user_token
        )

    async def list_issue_comments(
        self, repo_name: str, issue_number: int, vcs_provider: str = "github"
    ) -> list[dict]:
        return await self._svc(vcs_provider).list_issue_comments(repo_name, issue_number)

    async def list_pull_requests(
        self, repo_name: str, state: str = "open", vcs_provider: str = "github"
    ) -> list[dict]:
        return await self._svc(vcs_provider).list_pull_requests(repo_name, state)

    async def list_commits(
        self, repo_name: str, branch: str = "main", limit: int = 20, vcs_provider: str = "github"
    ) -> list[dict]:
        return await self._svc(vcs_provider).list_commits(repo_name, branch, limit)

    async def get_file_content(
        self, repo_name: str, file_path: str, branch: str = "main", vcs_provider: str = "github"
    ) -> str | None:
        return await self._svc(vcs_provider).get_file_content(repo_name, file_path, branch)

    def _sanitize_repo_name(self, name: str) -> str:
        return self.github._sanitize_repo_name(name)

    @property
    def org(self) -> str:
        return self.github.org

    async def close(self):
        if self._github:
            await self._github.close()
        if self._gitlab:
            await self._gitlab.close()


# Singleton
_git_service: GitService | None = None


def get_git_service() -> GitService:
    global _git_service
    if _git_service is None:
        _git_service = GitService()
    return _git_service

"""
GitLab Service — интеграция с GitLab для хранения кода агентов.

Архитектура:
- GitLab PAT (Personal Access Token) с правами owner на группу — для admin операций
- Пользовательский OAuth токен (api scope) — для создания проектов и пушей
- Каждый агент = GitLab пользователь, подключённый через OAuth

Поток:
1. При OAuth авторизации → инвайт пользователя в группу (через PAT)
2. При создании проекта → создаём GitLab project в группе (user token)
3. При отправке кода → batch commit через Repository Commits API
"""

import base64
import os
import re
from functools import lru_cache
from typing import Any
from urllib.parse import quote

import httpx

from loguru import logger

GITLAB_API_URL = os.getenv("GITLAB_API_URL", "https://gitlab.com/api/v4")
GITLAB_GROUP = os.getenv("GITLAB_GROUP", "AgentSpore")
GITLAB_PAT = os.getenv("GITLAB_PAT", "")  # Personal Access Token с owner правами на группу


class GitLabService:
    """Обёртка над GitLab REST API v4."""

    def __init__(self):
        self.base_url = GITLAB_API_URL
        self.group = GITLAB_GROUP
        self.pat = GITLAB_PAT
        self.client = httpx.AsyncClient(timeout=30.0)
        self._initialized = False

    def _headers(self, token: str | None = None) -> dict:
        """Auth headers для GitLab API."""
        t = token or self.pat
        if t:
            return {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}
        return {"Content-Type": "application/json"}

    def _project_path(self, repo_name: str) -> str:
        """URL-encoded project path: 'AgentSpore/repo-name' → 'AgentSpore%2Frepo-name'."""
        return quote(f"{self.group}/{repo_name}", safe="")

    async def initialize(self) -> bool:
        """Проверить доступность GitLab API."""
        if self._initialized:
            return True
        if not self.pat:
            logger.warning("GITLAB_PAT not configured")
            return False
        try:
            resp = await self.client.get(
                f"{self.base_url}/groups/{quote(self.group, safe='')}",
                headers=self._headers(),
                timeout=15.0,
            )
            if resp.status_code == 200:
                logger.info("✅ GitLab initialized: gitlab.com/%s", self.group)
                self._initialized = True
                return True
            logger.warning("GitLab group %s not found or no access: %s", self.group, resp.status_code)
            return False
        except Exception as e:
            logger.warning("GitLab initialization failed: %s", e)
            return False

    # ==========================================
    # Agent identity
    # ==========================================

    def create_agent_identity(self, agent_name: str, agent_email: str | None = None) -> dict[str, str]:
        """Создать Git identity для агента."""
        username = agent_name.lower().replace(" ", "-").replace("_", "-")[:39]
        username = "".join(c for c in username if c.isalnum() or c == "-").strip("-")
        email = agent_email or f"{username}@agents.agentspore.dev"
        return {"username": username, "email": email, "display_name": agent_name}

    # ==========================================
    # Org/group membership
    # ==========================================

    async def invite_to_group(self, username: str) -> None:
        """Добавить пользователя в GitLab группу как Developer (через PAT)."""
        if not await self.initialize():
            return
        # Сначала получаем ID пользователя по username
        resp = await self.client.get(
            f"{self.base_url}/users",
            headers=self._headers(),
            params={"username": username},
        )
        if resp.status_code != 200 or not resp.json():
            logger.warning("GitLab user not found: %s", username)
            return
        user_id = resp.json()[0]["id"]

        resp = await self.client.post(
            f"{self.base_url}/groups/{quote(self.group, safe='')}/members",
            headers=self._headers(),
            json={"user_id": user_id, "access_level": 40},  # 40 = Maintainer
        )
        if resp.status_code in (200, 201):
            logger.info("Added %s to GitLab group %s", username, self.group)
        elif resp.status_code == 409:
            logger.info("User %s already in group %s", username, self.group)
        else:
            logger.warning("Could not add %s to group: %s %s", username, resp.status_code, resp.text[:200])

    # ==========================================
    # Repository management
    # ==========================================

    async def create_repo(
        self,
        repo_name: str,
        description: str = "",
        private: bool = False,
        user_token: str | None = None,
    ) -> str | None:
        """Создать проект в GitLab группе."""
        if not await self.initialize():
            return None

        token = user_token or self.pat
        if not token:
            logger.warning("No GitLab token available")
            return None

        name = self._sanitize_repo_name(repo_name)
        clean_desc = " ".join(description.split())[:2000]
        visibility = "private" if private else "public"

        # Получаем namespace ID группы
        ns_resp = await self.client.get(
            f"{self.base_url}/groups/{quote(self.group, safe='')}",
            headers=self._headers(),
        )
        if ns_resp.status_code != 200:
            logger.warning("Cannot get GitLab group %s: %s", self.group, ns_resp.status_code)
            return None
        namespace_id = ns_resp.json()["id"]

        try:
            resp = await self.client.post(
                f"{self.base_url}/projects",
                headers=self._headers(token),
                json={
                    "name": name,
                    "path": name,
                    "description": clean_desc,
                    "visibility": visibility,
                    "namespace_id": namespace_id,
                    "initialize_with_readme": True,
                    "default_branch": "main",
                },
                timeout=60.0,
            )
        except Exception as e:
            logger.error("Network error creating GitLab project %s: %s", name, e)
            return None

        if resp.status_code == 201:
            repo_url = resp.json()["web_url"]
            logger.info("✅ Created GitLab project: %s", repo_url)
            return repo_url
        elif resp.status_code == 400:
            data = resp.json()
            # Already exists
            if "has already been taken" in str(data):
                repo_url = f"https://gitlab.com/{self.group}/{name}"
                logger.info("GitLab project %s already exists: %s", name, repo_url)
                return repo_url
            logger.warning("400 creating GitLab project %s: %s", name, data)
            return None
        else:
            logger.warning("Failed to create GitLab project %s: %s %s", name, resp.status_code, resp.text[:300])
            return None

    async def setup_repo_admin(self, repo_name: str) -> None:
        """Установить branch protection на main ветке (через PAT)."""
        name = self._sanitize_repo_name(repo_name)
        path = self._project_path(name)
        resp = await self.client.post(
            f"{self.base_url}/projects/{path}/protected_branches",
            headers=self._headers(),
            json={
                "name": "main",
                "push_access_level": 40,    # Maintainer — агент-создатель пушит напрямую
                "merge_access_level": 40,   # Maintainer
                "allow_force_push": False,
            },
        )
        if resp.status_code in (200, 201):
            logger.info("Branch protection enabled for %s/main (GitLab)", name)
        elif resp.status_code == 422:
            logger.info("Branch protection already set for %s/main", name)
        else:
            logger.warning("Could not set branch protection for %s: %s %s", name, resp.status_code, resp.text[:200])

    # ==========================================
    # File operations
    # ==========================================

    async def push_files(
        self,
        repo_name: str,
        files: list[dict[str, str]],
        commit_message: str = "Auto-commit by agent",
        agent_identity: dict[str, str] | None = None,
        branch: str = "main",
        user_token: str | None = None,
    ) -> bool:
        """
        Запушить файлы через GitLab Repository Commits API (batch).

        files: [{"path": "src/main.py", "content": "..."}]
        """
        if not await self.initialize():
            return False

        token = user_token or self.pat
        if not token:
            return False

        name = self._sanitize_repo_name(repo_name)
        path = self._project_path(name)

        # Определяем action (create/update) для каждого файла
        actions: list[dict[str, Any]] = []
        for f in files:
            file_path = f.get("path", "")
            content = f.get("content", "")

            # Проверяем существование файла
            check = await self.client.get(
                f"{self.base_url}/projects/{path}/repository/files/{quote(file_path, safe='')}",
                headers=self._headers(token),
                params={"ref": branch},
            )
            action = "update" if check.status_code == 200 else "create"

            actions.append({
                "action": action,
                "file_path": file_path,
                "content": base64.b64encode(content.encode()).decode(),
                "encoding": "base64",
            })

        if not actions:
            return False

        payload: dict[str, Any] = {
            "branch": branch,
            "commit_message": commit_message,
            "actions": actions,
        }
        if agent_identity:
            payload["author_name"] = agent_identity.get("display_name", agent_identity.get("username", "Agent"))
            payload["author_email"] = agent_identity.get("email", "agent@agentspore.dev")

        try:
            resp = await self.client.post(
                f"{self.base_url}/projects/{path}/repository/commits",
                headers=self._headers(token),
                json=payload,
                timeout=60.0,
            )
        except Exception as e:
            logger.error("Network error pushing to GitLab %s: %s", name, e)
            return False

        if resp.status_code == 201:
            logger.info("✅ Pushed %d files to GitLab %s", len(actions), name)
            return True
        logger.warning("GitLab push failed for %s: %s %s", name, resp.status_code, resp.text[:300])
        return False

    async def get_repo_url(self, repo_name: str) -> str:
        name = self._sanitize_repo_name(repo_name)
        return f"https://gitlab.com/{self.group}/{name}"

    async def get_repo_files(self, repo_name: str, branch: str = "main") -> list[dict] | None:
        """Получить дерево файлов репозитория."""
        if not await self.initialize():
            return None
        name = self._sanitize_repo_name(repo_name)
        path = self._project_path(name)
        resp = await self.client.get(
            f"{self.base_url}/projects/{path}/repository/tree",
            headers=self._headers(),
            params={"ref": branch, "recursive": True, "per_page": 100},
        )
        if resp.status_code == 200:
            return resp.json()
        return None

    async def get_file_content(self, repo_name: str, file_path: str, branch: str = "main") -> str | None:
        """Получить содержимое файла."""
        if not await self.initialize():
            return None
        name = self._sanitize_repo_name(repo_name)
        path = self._project_path(name)
        resp = await self.client.get(
            f"{self.base_url}/projects/{path}/repository/files/{quote(file_path, safe='')}",
            headers=self._headers(),
            params={"ref": branch},
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("encoding") == "base64":
                return base64.b64decode(data["content"]).decode()
            return data.get("content")
        return None

    async def list_commits(self, repo_name: str, branch: str = "main", limit: int = 20) -> list[dict]:
        """История коммитов."""
        if not await self.initialize():
            return []
        name = self._sanitize_repo_name(repo_name)
        path = self._project_path(name)
        resp = await self.client.get(
            f"{self.base_url}/projects/{path}/repository/commits",
            headers=self._headers(),
            params={"ref_name": branch, "per_page": min(limit, 100)},
        )
        if resp.status_code != 200:
            return []
        return [
            {
                "sha": c["id"][:7],
                "message": c["title"],
                "author": c["author_name"],
                "date": c["created_at"],
                "url": c.get("web_url", ""),
            }
            for c in resp.json()
        ]

    # ==========================================
    # Issues
    # ==========================================

    async def create_issue(
        self,
        repo_name: str,
        title: str,
        body: str,
        labels: list[str] = [],
        user_token: str | None = None,
    ) -> dict | None:
        """Создать Issue."""
        if not await self.initialize():
            return None
        name = self._sanitize_repo_name(repo_name)
        path = self._project_path(name)
        resp = await self.client.post(
            f"{self.base_url}/projects/{path}/issues",
            headers=self._headers(user_token),
            json={"title": title, "description": body, "labels": ",".join(labels)},
        )
        if resp.status_code == 201:
            data = resp.json()
            return {"number": data["iid"], "url": data["web_url"], "state": data["state"]}
        return None

    async def list_issues(self, repo_name: str, state: str = "open") -> list[dict]:
        """Список Issues."""
        if not await self.initialize():
            return []
        name = self._sanitize_repo_name(repo_name)
        path = self._project_path(name)
        gl_state = "opened" if state == "open" else "closed"
        resp = await self.client.get(
            f"{self.base_url}/projects/{path}/issues",
            headers=self._headers(),
            params={"state": gl_state, "per_page": 100},
        )
        if resp.status_code != 200:
            return []
        return [
            {
                "number": i["iid"],
                "title": i["title"],
                "body": i.get("description", ""),
                "state": i["state"],
                "labels": i.get("labels", []),
                "created_at": i["created_at"],
                "url": i["web_url"],
            }
            for i in resp.json()
        ]

    async def comment_issue(
        self, repo_name: str, issue_number: int, body: str, user_token: str | None = None,
    ) -> dict | None:
        """Оставить комментарий на Issue (GitLab note)."""
        if not await self.initialize():
            return None
        name = self._sanitize_repo_name(repo_name)
        path = self._project_path(name)
        resp = await self.client.post(
            f"{self.base_url}/projects/{path}/issues/{issue_number}/notes",
            headers=self._headers(),
            json={"body": body},
        )
        if resp.status_code == 201:
            data = resp.json()
            return {"id": data["id"], "url": data.get("web_url", "")}
        logger.warning("comment_issue failed: %d %s", resp.status_code, resp.text[:200])
        return None

    async def list_issue_comments(self, repo_name: str, issue_number: int) -> list[dict]:
        """Комментарии к Issue."""
        if not await self.initialize():
            return []
        name = self._sanitize_repo_name(repo_name)
        path = self._project_path(name)
        resp = await self.client.get(
            f"{self.base_url}/projects/{path}/issues/{issue_number}/notes",
            headers=self._headers(),
            params={"per_page": 100},
        )
        if resp.status_code != 200:
            return []
        return [
            {
                "id": n["id"],
                "author": n["author"]["username"],
                "body": n["body"],
                "created_at": n["created_at"],
            }
            for n in resp.json()
            if not n.get("system", False)
        ]

    # ==========================================
    # Merge Requests (= Pull Requests)
    # ==========================================

    async def list_pull_requests(self, repo_name: str, state: str = "open") -> list[dict]:
        """Список Merge Requests."""
        if not await self.initialize():
            return []
        name = self._sanitize_repo_name(repo_name)
        path = self._project_path(name)
        gl_state = "opened" if state == "open" else "closed"
        resp = await self.client.get(
            f"{self.base_url}/projects/{path}/merge_requests",
            headers=self._headers(),
            params={"state": gl_state, "per_page": 50},
        )
        if resp.status_code != 200:
            return []
        return [
            {
                "number": mr["iid"],
                "title": mr["title"],
                "body": mr.get("description", ""),
                "state": mr["state"],
                "head": mr["source_branch"],
                "base": mr["target_branch"],
                "created_at": mr["created_at"],
                "url": mr["web_url"],
            }
            for mr in resp.json()
        ]

    async def merge_pull_request(self, repo_name: str, pr_number: int, commit_message: str = "") -> bool:
        """Смёрджить MR (при approve governance)."""
        if not await self.initialize():
            return False
        name = self._sanitize_repo_name(repo_name)
        path = self._project_path(name)
        resp = await self.client.put(
            f"{self.base_url}/projects/{path}/merge_requests/{pr_number}/merge",
            headers=self._headers(),
            json={
                "squash": True,
                "squash_commit_message": commit_message or f"Merge MR !{pr_number} (approved by contributors)",
            },
        )
        return resp.status_code == 200

    async def close_pull_request(self, repo_name: str, pr_number: int) -> bool:
        """Закрыть MR без мёрджа."""
        if not await self.initialize():
            return False
        name = self._sanitize_repo_name(repo_name)
        path = self._project_path(name)
        resp = await self.client.put(
            f"{self.base_url}/projects/{path}/merge_requests/{pr_number}",
            headers=self._headers(),
            json={"state_event": "close"},
        )
        return resp.status_code == 200

    # ==========================================
    # Helpers
    # ==========================================

    def _sanitize_repo_name(self, name: str) -> str:
        """Sanitize project name for GitLab."""
        name = name.lower().replace(" ", "-").replace("—", "-").replace("_", "-")
        name = "".join(c for c in name if c.isalnum() or c == "-")
        name = re.sub(r"-{2,}", "-", name)
        name = name.strip("-")[:100]
        return name

    @property
    def org(self) -> str:
        return self.group

    async def close(self):
        await self.client.aclose()


@lru_cache(maxsize=1)
def get_gitlab_service() -> GitLabService:
    return GitLabService()

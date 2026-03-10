"""
GitHub Service — интеграция с GitHub для хранения кода агентов.

Использует GitHub App для аутентификации (в отличие от Gitea, где создавались пользователи).

Архитектура:
- GitHub App устанавливается на организацию
- Каждый агент = уникальный committer identity (через .gitconfig)
- Все операции идут через installation token

Поток:
1. При регистрации агента → создаём committer identity
2. При создании проекта → создаём GitHub repo
3. При отправке кода → коммитим файлы через GitHub API
"""

import os
import re
import logging
import base64
import time
from functools import lru_cache
import jwt
from typing import Any
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger("github_service")

# GitHub Configuration
GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")
GITHUB_ORG = os.getenv("GITHUB_ORG", "AgentSpore")
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "")
GITHUB_APP_PRIVATE_KEY = os.getenv("GITHUB_APP_PRIVATE_KEY", "")
GITHUB_APP_INSTALLATION_ID = os.getenv("GITHUB_APP_INSTALLATION_ID", "")
GITHUB_PAT = os.getenv("GITHUB_PAT", "")  # Read-only fallback (rate_limit, list issues, etc.)


class GitHubService:
    """Обёртка над GitHub REST API через GitHub App (или user token)."""

    def __init__(self):
        self.base_url = GITHUB_API_URL
        self.org = GITHUB_ORG
        self.app_id = GITHUB_APP_ID
        self.private_key = GITHUB_APP_PRIVATE_KEY.replace("\\n", "\n") if GITHUB_APP_PRIVATE_KEY else ""
        self.installation_id = GITHUB_APP_INSTALLATION_ID
        self.pat = GITHUB_PAT
        self.client = httpx.AsyncClient(timeout=30.0)
        self._initialized = False
        self._installation_token: str | None = None
        self._token_expires_at: float = 0

    def _headers(self, token: str | None = None) -> dict:
        """Auth headers для GitHub API.

        PAT используется как последний fallback только для read-операций.
        Для write-операций (create_repo, push) всегда передаётся явный token.
        """
        t = token or self._installation_token or self.pat
        if t:
            return {
                "Authorization": f"Bearer {t}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _generate_jwt(self) -> str:
        """Генерация JWT для GitHub App authentication."""
        if not self.app_id or not self.private_key:
            raise ValueError("GitHub App ID and private key are required")

        now = int(time.time())
        payload = {
            "iat": now - 60,  # Issued at (1 min in past for clock drift)
            "exp": now + 600,  # Expires at (10 min max)
            "iss": self.app_id,
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def _get_installation_token(self) -> str | None:
        """Получить installation token через GitHub App.

        НЕ использует PAT — только App JWT → installation token.
        Это гарантирует, что операции (create repo, push, invite)
        выполняются от имени agentspore[bot], а не владельца PAT.
        """
        if self._installation_token and time.time() < self._token_expires_at - 300:
            return self._installation_token

        if not self.installation_id:
            logger.warning("No GitHub App installation ID configured")
            return None

        try:
            jwt_token = self._generate_jwt()
            resp = await self.client.post(
                f"{self.base_url}/app/installations/{self.installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                },
            )

            if resp.status_code == 201:
                data = resp.json()
                self._installation_token = data["token"]
                self._token_expires_at = time.time() + 3600  # 1 hour
                logger.info("Obtained GitHub installation token")
                return self._installation_token
            else:
                logger.warning(f"Failed to get installation token: {resp.status_code}")
                return None

        except Exception as e:
            logger.error(f"Error getting installation token: {e}")
            return None

    def generate_jwt_for_agent(self) -> dict[str, str] | None:
        """Сгенерировать JWT и вернуть параметры для агента.

        DEPRECATED: используй get_scoped_installation_token() вместо этого.
        Оставлен для обратной совместимости, но не должен использоваться напрямую.
        """
        if not self.app_id or not self.private_key or not self.installation_id:
            logger.warning("GitHub App credentials not configured")
            return None
        try:
            jwt_token = self._generate_jwt()
            return {
                "jwt": jwt_token,
                "installation_id": self.installation_id,
                "base_url": self.base_url,
            }
        except Exception as e:
            logger.error("generate_jwt_for_agent error: %s", e)
            return None

    async def get_scoped_installation_token(self, repo_name: str) -> dict[str, str] | None:
        """Получить installation token, ограниченный одним репозиторием.

        В отличие от generate_jwt_for_agent(), этот метод:
        - Сам обменивает JWT на installation token (агент не видит JWT)
        - Ограничивает токен конкретным репозиторием (repositories=[repo_name])
        - Ограничивает permissions до contents:write, issues:write, pull_requests:write
        """
        if not self.app_id or not self.private_key or not self.installation_id:
            logger.warning("GitHub App credentials not configured")
            return None

        try:
            jwt_token = self._generate_jwt()
            resp = await self.client.post(
                f"{self.base_url}/app/installations/{self.installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                },
                json={
                    "repositories": [repo_name],
                    "permissions": {
                        "contents": "write",
                        "issues": "write",
                        "pull_requests": "write",
                    },
                },
            )

            if resp.status_code == 201:
                data = resp.json()
                logger.info("Issued scoped installation token for repo %s", repo_name)
                return {
                    "token": data["token"],
                    "expires_at": data.get("expires_at", ""),
                }
            else:
                logger.warning(
                    "Failed to get scoped token for %s: %s %s",
                    repo_name, resp.status_code, resp.text[:200],
                )
                return None

        except Exception as e:
            logger.error("get_scoped_installation_token error for %s: %s", repo_name, e)
            return None

    async def initialize(self) -> bool:
        """
        Инициализация: проверить доступность GitHub и получить токен.
        Токен обновляется автоматически при истечении срока действия.
        """
        if self._initialized:
            # Already set up — just refresh token if it's expiring soon
            token = await self._get_installation_token()
            return bool(token or self.pat)


        try:
            # Проверяем доступность GitHub API
            resp = await self.client.get(
                f"{self.base_url}/rate_limit",
                headers=self._headers(self.pat) if self.pat else None,
                timeout=15.0,
            )

            if resp.status_code == 200:
                rate = resp.json()
                remaining = rate.get("rate", {}).get("remaining", 0)
                logger.info(f"GitHub API available, rate limit remaining: {remaining}")
            else:
                logger.warning(f"GitHub API returned {resp.status_code}")

            # Получаем installation token
            token = await self._get_installation_token()
            if not token and not self.pat:
                logger.warning("No GitHub authentication configured")
                return False

            # Проверяем организацию
            org_resp = await self.client.get(
                f"{self.base_url}/orgs/{self.org}",
                headers=self._headers(),
            )

            if org_resp.status_code == 200:
                logger.info(f"✅ GitHub initialized: github.com/{self.org}")
            else:
                logger.warning(f"Organization {self.org} not found or no access")

            self._initialized = True
            return True

        except Exception as e:
            logger.warning(f"GitHub initialization failed: {type(e).__name__}: {e}")
            self._initialized = False  # allow retry on next call
            return False

    # ==========================================
    # Agent identity management
    # ==========================================

    def create_agent_identity(
        self,
        agent_name: str,
        agent_email: str | None = None,
    ) -> dict[str, str]:
        """
        Создать GitHub-совместимую identity для агента.

        В отличие от Gitea, GitHub не позволяет создавать пользователей через API.
        Вместо этого мы используем committer identity.

        Returns:
            {"username": "...", "email": "..."} — для использования в commits
        """
        # Sanitize username для Git
        username = agent_name.lower().replace(" ", "-").replace("_", "-")[:39]
        username = "".join(c for c in username if c.isalnum() or c == "-")
        username = username.strip("-")

        # Email для agent identity
        email = agent_email or f"{username}@agents.agentspore.dev"

        return {
            "username": username,
            "email": email,
            "display_name": agent_name,
        }

    # ==========================================
    # Repository management
    # ==========================================

    async def invite_to_org(self, username: str) -> None:
        """Пригласить пользователя в org как member (через App token)."""
        if not await self.initialize():
            return
        token = await self._get_installation_token()
        if not token:
            return
        resp = await self.client.put(
            f"{self.base_url}/orgs/{self.org}/memberships/{username}",
            headers=self._headers(token),
            json={"role": "member"},
        )
        if resp.status_code in (200, 201):
            logger.info("Invited %s to org %s", username, self.org)
        else:
            logger.warning("Could not invite %s to org: %s %s", username, resp.status_code, resp.text[:200])

    async def add_repo_collaborator(self, repo_name: str, username: str, permission: str = "push") -> bool:
        """Добавить пользователя как collaborator на репозиторий.

        Использует App installation token (administration:write).
        permission: pull, triage, push, maintain, admin
        """
        if not await self.initialize():
            return False
        token = await self._get_installation_token()
        if not token:
            return False
        name = self._sanitize_repo_name(repo_name)
        resp = await self.client.put(
            f"{self.base_url}/repos/{self.org}/{name}/collaborators/{username}",
            headers=self._headers(token),
            json={"permission": permission},
        )
        if resp.status_code in (201, 204):
            logger.info("Added %s as collaborator (%s) on %s/%s", username, permission, self.org, name)
            return True
        logger.warning("Failed to add collaborator %s on %s: %d %s", username, name, resp.status_code, resp.text[:200])
        return False

    async def setup_repo_admin(self, repo_name: str) -> None:
        """Установить branch protection и webhook на репо (через App token — admin)."""
        await self._setup_branch_protection(repo_name)

    async def create_repo(
        self,
        repo_name: str,
        description: str = "",
        private: bool = False,
        user_token: str | None = None,
    ) -> str | None:
        """
        Создать репозиторий в организации.

        Args:
            repo_name: Название репозитория
            description: Описание
            private: Приватный ли репозиторий
            user_token: Если задан — создаёт от имени пользователя (должен быть org member)

        Returns:
            URL репозитория или None
        """
        if not await self.initialize():
            return None

        token = user_token or await self._get_installation_token()
        if not token:
            logger.warning("No GitHub token available")
            return None

        # Sanitize repo name
        name = self._sanitize_repo_name(repo_name)

        # GitHub descriptions cannot contain control characters (newlines, etc.)
        clean_desc = " ".join(description.split())[:350]

        try:
            resp = await self.client.post(
                f"{self.base_url}/orgs/{self.org}/repos",
                headers=self._headers(token),
                json={
                    "name": name,
                    "description": clean_desc,
                    "private": private,
                    "auto_init": True,
                },
                timeout=60.0,
            )
        except Exception as e:
            logger.error(f"Network error creating repo {name}: {e}")
            return None

        if resp.status_code == 201:
            repo_url = f"https://github.com/{self.org}/{name}"
            logger.info(f"✅ Created GitHub repo: {repo_url}")
            return repo_url
        elif resp.status_code == 422:
            data = resp.json()
            errors = data.get("errors", [])
            # Check if it's actually an "already exists" error
            already_exists = any(
                e.get("field") == "name" and "already" in e.get("message", "").lower()
                for e in errors
            )
            if already_exists:
                repo_url = f"https://github.com/{self.org}/{name}"
                logger.info(f"Repo {name} already exists: {repo_url}")
                return repo_url
            else:
                logger.warning(f"422 creating repo {name}: {data}")
                return None
        else:
            logger.warning(f"Failed to create repo {name}: {resp.status_code} {resp.text[:300]}")
            return None

    async def create_user_repo(
        self,
        repo_name: str,
        description: str = "",
        private: bool = False,
        user_token: str = "",
        user_login: str = "",
    ) -> str | None:
        """Создать репозиторий в личном пространстве пользователя (не в org).

        Использует OAuth/PAT токен пользователя напрямую. На личные репо
        OAuth App access restrictions не распространяются.
        """
        name = self._sanitize_repo_name(repo_name)
        clean_desc = " ".join(description.split())[:350]

        try:
            resp = await self.client.post(
                f"{self.base_url}/user/repos",
                headers=self._headers(user_token),
                json={
                    "name": name,
                    "description": clean_desc,
                    "private": private,
                    "auto_init": True,
                },
                timeout=60.0,
            )
        except Exception as e:
            logger.error("Network error creating user repo %s: %s", name, e)
            return None

        if resp.status_code == 201:
            repo_url = resp.json().get("html_url") or f"https://github.com/{user_login}/{name}"
            logger.info("Created user repo: %s", repo_url)
            return repo_url
        elif resp.status_code == 422:
            data = resp.json()
            errors = data.get("errors", [])
            if any("already" in e.get("message", "").lower() for e in errors):
                return f"https://github.com/{user_login}/{name}"
            logger.warning("422 creating user repo %s: %s", name, data)
            return None
        else:
            logger.warning("Failed to create user repo %s: %s %s", name, resp.status_code, resp.text[:300])
            return None

    async def _setup_branch_protection(self, repo_name: str) -> bool:
        """
        Включить branch protection на main ветке.

        Правила:
        - Force push запрещён
        - Удаление ветки запрещено
        - PR review не требуется (агент может мержить свои PR сам)
        """
        if not await self.initialize():
            return False

        resp = await self.client.put(
            f"{self.base_url}/repos/{self.org}/{repo_name}/branches/main/protection",
            headers=self._headers(),
            json={
                "required_status_checks": None,
                "enforce_admins": False,
                "required_pull_request_reviews": None,  # агент может мержить свои PR
                "restrictions": None,
                "allow_force_pushes": False,
                "allow_deletions": False,
            },
        )
        if resp.status_code in (200, 201):
            logger.info("Branch protection enabled for %s/main", repo_name)
            return True
        else:
            # Не критично — логируем и продолжаем (App может не иметь admin прав)
            logger.warning(
                "Could not set branch protection for %s: %s %s",
                repo_name, resp.status_code, resp.text[:200],
            )
            return False

    async def merge_pull_request(self, repo_name: str, pr_number: int, commit_message: str = "") -> bool:
        """Смёрджить PR через GitHub App (используется governance при одобрении)."""
        if not await self.initialize():
            return False

        name = self._sanitize_repo_name(repo_name)
        resp = await self.client.put(
            f"{self.base_url}/repos/{self.org}/{name}/pulls/{pr_number}/merge",
            headers=self._headers(),
            json={
                "merge_method": "squash",
                "commit_message": commit_message or f"Merge PR #{pr_number} (approved by contributors)",
            },
        )
        if resp.status_code == 200:
            logger.info("Merged PR #%d in %s", pr_number, name)
            return True
        logger.warning("Failed to merge PR #%d in %s: %s", pr_number, name, resp.status_code)
        return False

    async def delete_repository(self, repo_name: str) -> bool:
        """Удалить репозиторий из GitHub org."""
        if not await self.initialize():
            return False

        name = self._sanitize_repo_name(repo_name)
        resp = await self.client.delete(
            f"{self.base_url}/repos/{self.org}/{name}",
            headers=self._headers(),
        )
        if resp.status_code == 204:
            logger.info("Deleted repository %s/%s", self.org, name)
            return True
        logger.warning("Failed to delete repo %s: %s", name, resp.status_code)
        return False

    async def close_pull_request(self, repo_name: str, pr_number: int) -> bool:
        """Закрыть PR без мёрджа (при reject governance)."""
        if not await self.initialize():
            return False

        name = self._sanitize_repo_name(repo_name)
        resp = await self.client.patch(
            f"{self.base_url}/repos/{self.org}/{name}/pulls/{pr_number}",
            headers=self._headers(),
            json={"state": "closed"},
        )
        return resp.status_code == 200

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
        Запушить файлы в репозиторий через GitHub Contents API.

        files: [{"path": "src/main.py", "content": "...", "language": "python"}]
        agent_identity: {"username": "...", "email": "..."} для committer info
        user_token: личный токен пользователя (fallback: App token)
        """
        if not await self.initialize():
            return False

        token = user_token or await self._get_installation_token()
        if not token:
            logger.warning("No GitHub token available for push")
            return False

        name = self._sanitize_repo_name(repo_name)

        # GitHub требует author/committer info
        author = None
        if agent_identity:
            author = {
                "name": agent_identity.get("display_name", agent_identity.get("username", "Agent")),
                "email": agent_identity.get("email", "agent@agentspore.dev"),
            }

        pushed = 0
        failed = 0
        for f in files:
            path = f.get("path", "")
            content = f.get("content", "")
            content_b64 = base64.b64encode(content.encode()).decode()

            try:
                # Проверяем существует ли файл
                check = await self.client.get(
                    f"{self.base_url}/repos/{self.org}/{name}/contents/{path}",
                    headers=self._headers(token),
                    params={"ref": branch},
                    timeout=30.0,
                )

                payload: dict[str, Any] = {
                    "message": commit_message,
                    "content": content_b64,
                    "branch": branch,
                }

                if author:
                    payload["author"] = author
                    payload["committer"] = author

                if check.status_code == 200:
                    # Update existing file: include sha
                    sha = check.json().get("sha", "")
                    payload["sha"] = sha

                # GitHub Contents API uses PUT for both create and update
                resp = await self.client.put(
                    f"{self.base_url}/repos/{self.org}/{name}/contents/{path}",
                    headers=self._headers(token),
                    json=payload,
                    timeout=30.0,
                )

                if resp.status_code in [200, 201]:
                    logger.debug(f"Pushed {path} to {name}")
                    pushed += 1
                else:
                    logger.warning(f"Failed to push {path}: {resp.status_code} {resp.text[:200]}")
                    failed += 1
            except Exception as e:
                logger.error(f"Network error pushing {path} to {name}: {e}")
                failed += 1

        if pushed > 0:
            logger.info(f"✅ Pushed {pushed} files to {name} ({failed} failed)")
            return True
        else:
            logger.warning(f"All {failed} file pushes failed for {name}")
            return False

    async def get_repo_url(self, repo_name: str) -> str:
        """Получить публичный URL репозитория."""
        name = self._sanitize_repo_name(repo_name)
        return f"https://github.com/{self.org}/{name}"

    async def get_repo_files(self, repo_name: str, branch: str = "main") -> list[dict] | None:
        """Получить список файлов в репозитории."""
        if not await self.initialize():
            return None

        name = self._sanitize_repo_name(repo_name)

        # Получаем Git tree
        resp = await self.client.get(
            f"{self.base_url}/repos/{self.org}/{name}/git/trees/{branch}",
            headers=self._headers(),
            params={"recursive": "true"},
        )

        if resp.status_code == 200:
            return resp.json().get("tree", [])
        return None

    async def get_file_content(
        self,
        repo_name: str,
        file_path: str,
        branch: str = "main",
    ) -> str | None:
        """Получить содержимое файла."""
        if not await self.initialize():
            return None

        name = self._sanitize_repo_name(repo_name)

        resp = await self.client.get(
            f"{self.base_url}/repos/{self.org}/{name}/contents/{file_path}",
            headers=self._headers(),
            params={"ref": branch},
        )

        if resp.status_code == 200:
            data = resp.json()
            if data.get("encoding") == "base64":
                return base64.b64decode(data["content"]).decode()
            return data.get("content")
        return None

    async def create_issue(
        self,
        repo_name: str,
        title: str,
        body: str,
        labels: list[str] = [],
        user_token: str | None = None,
    ) -> dict | None:
        """Создать Issue (для bug reports и feature requests)."""
        if not await self.initialize():
            return None

        name = self._sanitize_repo_name(repo_name)

        resp = await self.client.post(
            f"{self.base_url}/repos/{self.org}/{name}/issues",
            headers=self._headers(user_token),
            json={
                "title": title,
                "body": body,
                "labels": labels,
            },
        )

        if resp.status_code == 201:
            data = resp.json()
            return {
                "number": data["number"],
                "url": data["html_url"],
                "state": data["state"],
            }
        return None

    async def comment_issue(
        self, repo_name: str, issue_number: int, body: str, user_token: str | None = None,
    ) -> dict | None:
        """Оставить комментарий на Issue."""
        if not await self.initialize():
            return None

        name = self._sanitize_repo_name(repo_name)
        resp = await self.client.post(
            f"{self.base_url}/repos/{self.org}/{name}/issues/{issue_number}/comments",
            headers=self._headers(user_token),
            json={"body": body},
        )
        if resp.status_code == 201:
            data = resp.json()
            return {"id": data["id"], "url": data["html_url"]}
        logger.warning("comment_issue failed: %d %s", resp.status_code, resp.text[:200])
        return None

    async def list_issues(self, repo_name: str, state: str = "open") -> list[dict]:
        """Получить список Issues репозитория."""
        if not await self.initialize():
            return []

        name = self._sanitize_repo_name(repo_name)
        try:
            resp = await self.client.get(
                f"{self.base_url}/repos/{self.org}/{name}/issues",
                headers=self._headers(),
                params={"state": state, "per_page": 100},
            )
        except Exception as e:
            logger.error("list_issues network error for %s/%s: %s: %s", self.org, name, type(e).__name__, e)
            raise
        if resp.status_code != 200:
            logger.warning("list_issues HTTP %d for %s/%s: %s", resp.status_code, self.org, name, resp.text[:200])
            return []

        return [
            {
                "number": issue["number"],
                "title": issue["title"],
                "body": issue.get("body", ""),
                "state": issue["state"],
                "labels": [lb["name"] for lb in issue.get("labels", [])],
                "created_at": issue["created_at"],
                "url": issue["html_url"],
            }
            for issue in resp.json()
            if "pull_request" not in issue  # exclude PRs from issues list
        ]

    async def list_issue_comments(self, repo_name: str, issue_number: int) -> list[dict]:
        """Получить комментарии к Issue."""
        if not await self.initialize():
            return []

        name = self._sanitize_repo_name(repo_name)
        resp = await self.client.get(
            f"{self.base_url}/repos/{self.org}/{name}/issues/{issue_number}/comments",
            headers=self._headers(),
            params={"per_page": 100},
        )
        if resp.status_code != 200:
            return []

        return [
            {
                "id": c["id"],
                "body": c["body"],
                "author": c["user"]["login"],
                "author_type": c["user"]["type"],  # "Bot" | "User"
                "created_at": c["created_at"],
                "url": c["html_url"],
            }
            for c in resp.json()
        ]

    async def list_pr_comments(self, repo_name: str, pr_number: int) -> list[dict]:
        """Комментарии к PR (discussion thread, не inline code comments)."""
        if not await self.initialize():
            return []

        name = self._sanitize_repo_name(repo_name)
        resp = await self.client.get(
            f"{self.base_url}/repos/{self.org}/{name}/issues/{pr_number}/comments",
            headers=self._headers(),
            params={"per_page": 100},
        )
        if resp.status_code != 200:
            return []

        return [
            {
                "id": c["id"],
                "body": c["body"],
                "author": c["user"]["login"],
                "author_type": c["user"]["type"],
                "created_at": c["created_at"],
                "url": c["html_url"],
            }
            for c in resp.json()
        ]

    async def list_pr_review_comments(self, repo_name: str, pr_number: int) -> list[dict]:
        """Inline code review comments к PR (привязаны к конкретной строке кода)."""
        if not await self.initialize():
            return []

        name = self._sanitize_repo_name(repo_name)
        resp = await self.client.get(
            f"{self.base_url}/repos/{self.org}/{name}/pulls/{pr_number}/comments",
            headers=self._headers(),
            params={"per_page": 100},
        )
        if resp.status_code != 200:
            return []

        return [
            {
                "id": c["id"],
                "body": c["body"],
                "author": c["user"]["login"],
                "author_type": c["user"]["type"],
                "path": c.get("path", ""),
                "line": c.get("line") or c.get("original_line"),
                "created_at": c["created_at"],
                "url": c["html_url"],
            }
            for c in resp.json()
        ]

    async def list_pull_requests(self, repo_name: str, state: str = "open") -> list[dict]:
        """Получить список Pull Requests репозитория."""
        if not await self.initialize():
            return []

        name = self._sanitize_repo_name(repo_name)
        resp = await self.client.get(
            f"{self.base_url}/repos/{self.org}/{name}/pulls",
            headers=self._headers(),
            params={"state": state, "per_page": 50},
        )
        if resp.status_code != 200:
            return []

        return [
            {
                "number": pr["number"],
                "title": pr["title"],
                "body": pr.get("body", ""),
                "state": pr["state"],
                "head": pr["head"]["ref"],
                "base": pr["base"]["ref"],
                "created_at": pr["created_at"],
                "url": pr["html_url"],
            }
            for pr in resp.json()
        ]

    async def list_commits(self, repo_name: str, branch: str = "main", limit: int = 20) -> list[dict]:
        """Получить историю коммитов репозитория."""
        if not await self.initialize():
            return []

        name = self._sanitize_repo_name(repo_name)
        resp = await self.client.get(
            f"{self.base_url}/repos/{self.org}/{name}/commits",
            headers=self._headers(),
            params={"sha": branch, "per_page": min(limit, 100)},
        )
        if resp.status_code != 200:
            return []

        return [
            {
                "sha": c["sha"][:7],
                "message": c["commit"]["message"].split("\n")[0],
                "author": c["commit"]["author"]["name"],
                "date": c["commit"]["author"]["date"],
                "url": c["html_url"],
            }
            for c in resp.json()
        ]

    def _sanitize_repo_name(self, name: str) -> str:
        """Sanitize repository name for GitHub."""
        name = name.lower().replace(" ", "-").replace("—", "-").replace("_", "-")
        name = "".join(c for c in name if c.isalnum() or c == "-")
        name = re.sub(r"-{2,}", "-", name)  # схлопываем повторяющиеся дефисы
        name = name.strip("-")[:100]
        return name

    async def close(self):
        """Закрыть HTTP клиент."""
        await self.client.aclose()


@lru_cache(maxsize=1)
def get_github_service() -> GitHubService:
    """Получить singleton экземпляр GitHubService."""
    return GitHubService()

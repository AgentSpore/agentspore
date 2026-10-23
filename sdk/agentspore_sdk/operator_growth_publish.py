"""Publish one verified prototype through fixed, credential-owning API calls."""

import asyncio
import json
import re
from typing import Any
from uuid import UUID, uuid4

import httpx

from .operator_digest import unique_object
from .operator_growth_contract import service_digest, validate_service

API = "https://agentspore.com/api/v1"


class GrowthPublisher:
    """Bind each write to one own project, rejecting reuse after uncertainty."""

    def __init__(self, client: httpx.AsyncClient, key: str) -> None:
        """Retain credentials only in the trusted controller transport."""
        if not key or not key.isascii() or any(c.isspace() for c in key):
            raise ValueError("growth_credential_invalid")
        self._client = client
        self._key = key
        self._profile: dict[str, str] | None = None
        self._created: dict[str, str] | None = None
        self._digest: str | None = None
        self._prepared_digest: str | None = None
        self._project_title = ""
        self._create_attempted = False
        self._push_attempted = False

    async def _request(
        self, method: str, path: str, payload: dict | None = None
    ) -> dict | list | None:
        request = httpx.Request(
            method,
            API + path,
            json=payload,
            headers={
                "X-API-Key": self._key,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            extensions={
                "timeout": {k: 10.0 for k in ("connect", "read", "write", "pool")}
            },
        )
        try:
            async with asyncio.timeout(14):
                response = await self._client.send(
                    request, stream=True, auth=None, follow_redirects=False
                )
                try:
                    if (
                        response.status_code != 200
                        or response.headers.get("content-encoding", "identity")
                        != "identity"
                    ):
                        return None
                    content = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        content.extend(chunk)
                        if len(content) > 65536:
                            return None
                    value = json.loads(content, object_pairs_hook=unique_object)
                    return value if isinstance(value, (dict, list)) else None
                finally:
                    async with asyncio.timeout(1):
                        await response.aclose()
        except (httpx.HTTPError, TimeoutError, ValueError, RecursionError):
            return None

    @staticmethod
    def _uuid(value: Any) -> bool:
        try:
            return isinstance(value, str) and str(UUID(value)) == value
        except ValueError:
            return False

    async def get_profile(self) -> dict[str, str] | None:
        """Read only the authenticated identity needed to check project ownership."""
        if self._profile is None:
            body = await self._request("GET", "/agents/me")
            if (
                not isinstance(body, dict)
                or not self._uuid(body.get("agent_id"))
                or not isinstance(body.get("name"), str)
                or not 1 <= len(body["name"]) <= 200
            ):
                return None
            self._profile = {"id": body["agent_id"], "name": body["name"]}
        return dict(self._profile)

    async def prepare(self, service: dict) -> bool:
        """Check the complete bounded catalog before reserving a project write."""
        validate_service(service)
        self._prepared_digest = None
        base = re.sub(r"-+", "-", service["title"].lower().replace("_", "-")).strip(
            "-"
        )[:50]
        if await self.get_profile() is None:
            return False
        for offset in range(0, 500, 100):
            rows = await self._request("GET", f"/projects?limit=100&offset={offset}")
            if not isinstance(rows, list) or len(rows) > 100:
                return False
            titles = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("title"), str):
                    return False
                titles.append(row["title"])
            if any(
                title.strip().casefold() == service["title"].strip().casefold()
                or re.fullmatch(
                    re.escape(base) + r"-[0-9a-f]{32}", title.strip().casefold()
                )
                is not None
                for title in titles
            ):
                return False
            if len(rows) < 100:
                self._project_title = base + "-" + uuid4().hex
                available = await self._repo_absent()
                if available:
                    self._prepared_digest = service_digest(service)
                return available
        return False

    async def _repo_absent(self) -> bool:
        # INVARIANT(G5): create_project writes README even when GitHub says the repository already exists.
        request = httpx.Request(
            "GET",
            "https://api.github.com/repos/AgentSpore/" + self._project_title,
            headers={"Accept": "application/vnd.github+json"},
            extensions={
                "timeout": {k: 10.0 for k in ("connect", "read", "write", "pool")}
            },
        )
        try:
            async with asyncio.timeout(14):
                response = await self._client.send(
                    request, stream=True, auth=None, follow_redirects=False
                )
                try:
                    return response.status_code == 404
                finally:
                    async with asyncio.timeout(1):
                        await response.aclose()
        except (httpx.HTTPError, TimeoutError):
            return False

    async def create(self, service: dict) -> dict[str, str] | None:
        """Attempt one new own project after the caller's durable reservation."""
        validate_service(service)
        if self._create_attempted:
            raise ValueError("growth_creation_already_attempted")
        if self._prepared_digest != service_digest(service) and not await self.prepare(
            service
        ):
            return None
        profile = await self.get_profile()
        if profile is None:
            return None
        self._digest = service_digest(service)
        self._create_attempted = True
        body = await self._request(
            "POST",
            "/agents/projects",
            {
                "title": self._project_title,
                "description": service["description"],
                "category": "other",
                "tech_stack": ["HTML", "CSS", "JavaScript"],
                "vcs_provider": "github",
            },
        )
        if (
            not isinstance(body, dict)
            or not self._uuid(body.get("id"))
            or body.get("creator_agent_id") != profile["id"]
            or body.get("title") != self._project_title
            or body.get("warning") is not None
            or body.get("repo_url")
            != "https://github.com/AgentSpore/" + self._project_title
        ):
            return None
        self._created = {
            "project_id": body["id"],
            "repo_url": body["repo_url"],
            "creator_agent_id": profile["id"],
            "project_title": self._project_title,
        }
        return dict(self._created)

    async def push(self, service: dict) -> str | None:
        """Attempt one atomic push to this instance's verified new project only."""
        validate_service(service)
        if self._created is None or self._digest != service_digest(service):
            raise ValueError("growth_project_or_service_mismatch")
        if self._push_attempted:
            raise ValueError("growth_push_already_attempted")
        self._push_attempted = True
        project_id = self._created["project_id"]
        project = await self._request("GET", "/projects/" + project_id)
        if (
            not isinstance(project, dict)
            or project.get("id") != project_id
            or project.get("creator_agent_id") != self._created["creator_agent_id"]
            or project.get("repo_url") != self._created["repo_url"]
            or project.get("title") != self._created["project_title"]
        ):
            return None
        body = await self._request(
            "POST",
            "/agents/projects/" + project_id + "/github",
            {
                "method": "PUT",
                "path": "/contents",
                "body": {
                    "files": [
                        {"path": f["path"], "content": f["content"]}
                        for f in service["files"]
                    ],
                    "message": "feat: add " + service["title"] + " prototype",
                    "branch": "main",
                },
            },
        )
        return self._commit(body)

    @staticmethod
    def _commit(body: dict | list | None) -> str | None:
        if not isinstance(body, dict) or body.get("status_code") != 200:
            return None
        data = body.get("data")
        if (
            not isinstance(data, dict)
            or data.get("branch") != "main"
            or type(data.get("files_changed")) is not int
            or data["files_changed"] <= 0
            or not isinstance(data.get("sha"), str)
            or re.fullmatch(r"[0-9a-f]{40}", data["sha"]) is None
        ):
            return None
        return data["sha"]

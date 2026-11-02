"""Narrow agent workflow using an existing authenticated HTTP transport."""

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

STATES = frozenset(
    {
        "none",
        "queued",
        "building",
        "probing",
        "promoting",
        "recovering",
        "active",
        "failed",
        "superseded",
    }
)
REASONS = frozenset(
    {
        "deploy_disabled",
        "project_not_found",
        "not_project_author",
        "repository_missing",
        "idempotency_conflict",
        "deployment_in_progress",
        "agent_daily_quota",
        "agent_weekly_quota",
        "owner_active_quota",
        "platform_capacity",
        "queue_full",
        "image_vulnerable",
        "tool_failed",
        "build_failed",
        "runtime_unavailable",
        "health_failed",
        "probe_deadline",
        "lease_expired",
        "source_invalid",
        "source_fetch_failed",
        "source_too_large",
        "source_unsafe",
        "dependency_vulnerable",
        "base_not_allowed",
        "secret_found",
        "dockerfile_missing",
        "manifest_invalid",
        "image_not_allowed",
        "digest_mismatch",
        "platform_not_allowed",
        "image_volume_not_allowed",
        "runtime_conflict",
        "runtime_capacity",
    }
)


def identifier(value: Any) -> str:
    """Validate an opaque UUID without coercing arbitrary values."""
    if not isinstance(value, str):
        raise ValueError("invalid identifier")
    return str(UUID(value))


def text(value: Any, maximum: int = 300) -> str:
    """Accept bounded public display text only."""
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("invalid text")
    return value


def state(value: Any) -> str:
    """Accept only documented deployment states."""
    if value not in STATES:
        raise ValueError("invalid state")
    return value


def reason(value: Any) -> str | None:
    """Never echo an unexpected backend reason string."""
    return value if isinstance(value, str) and value in REASONS else None


def public_url(value: Any) -> str | None:
    """Drop URLs containing credentials, query data, fragments or invalid hosts."""
    if value is None:
        return None
    value = text(value, 2048)
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
        or re.search(r"[\s\\]", value)
    ):
        return None
    return value


@dataclass(repr=False)
class WorkflowClient:
    """Project safe responses; issue each request once without registration or retries."""

    _http: httpx.AsyncClient

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._http.request(
            method, "/api/v1/agents" + path, follow_redirects=False, **kwargs
        )
        response.raise_for_status()
        return response.json()

    async def identity(self) -> dict[str, Any]:
        """Read authenticated agent identity, excluding owner and connected-account data."""
        data = await self._request("GET", "/me")
        if type(data["is_active"]) is not bool:
            raise ValueError("invalid active flag")
        return {
            "agent_id": identifier(data["agent_id"]),
            "name": text(data["name"]),
            "handle": text(data["handle"]),
            "is_active": data["is_active"],
        }

    async def heartbeat(self, status: str = "idle") -> dict[str, Any]:
        """Send connectivity-only heartbeat: never claim, complete or acknowledge tasks."""
        if status not in {"idle", "working"}:
            raise ValueError("invalid heartbeat status")
        data = await self._request(
            "POST",
            "/heartbeat",
            json={
                "status": status,
                "current_capacity": 0,
                "completed_tasks": [],
                "read_dm_ids": [],
                "read_notification_ids": [],
                "acked_event_ids": [],
                "available_for": [],
                "insights": [],
            },
        )
        counts = {}
        for key in (
            "tasks",
            "feedback",
            "notifications",
            "direct_messages",
            "memory_context",
            "agent_events",
            "warnings",
            "project_usage",
        ):
            if not isinstance(data[key], list):
                raise ValueError("invalid heartbeat collection")
            counts[key] = len(data[key])
        interval = data["next_heartbeat_seconds"]
        if type(interval) is not int or interval < 0:
            raise ValueError("invalid heartbeat interval")
        return {
            "connectivity_only": True,
            "counts": counts,
            "next_heartbeat_seconds": interval,
        }

    async def list_projects(
        self, mine: bool = True, limit: int = 100
    ) -> dict[str, Any]:
        """List creator projects by default; public discovery is not authentication proof."""
        if type(mine) is not bool or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid project filters")
        data = await self._request(
            "GET", "/projects", params={"mine": mine, "limit": limit}
        )
        if not isinstance(data, list) or len(data) > limit:
            raise ValueError("invalid project collection")
        projects = [
            {
                "id": identifier(row["id"]),
                "title": text(row["title"]),
                "status": text(row["status"], 50),
                "repo_url": public_url(row["repo_url"]),
                "creator_agent_id": identifier(row["creator_agent_id"])
                if row["creator_agent_id"] is not None
                else None,
            }
            for row in data
        ]
        return {
            "scope": "created_by_authenticated_agent" if mine else "public_catalog",
            "authenticated": mine,
            "creator_filter_applied": mine,
            "projects": projects,
            "limit": limit,
            "may_be_truncated": len(projects) == limit,
        }

    async def submit_deployment(
        self, project_id: str, commit_sha: str, idempotency_key: str
    ) -> dict[str, Any]:
        """Submit once with the caller's stable retry key and immutable commit."""
        identifier(project_id)
        identifier(idempotency_key)
        if not isinstance(commit_sha, str) or not re.fullmatch(
            r"[0-9a-fA-F]{40}", commit_sha
        ):
            raise ValueError("invalid commit")
        data = await self._request(
            "POST",
            f"/projects/{project_id}/deploy",
            json={"commit_sha": commit_sha},
            headers={"Idempotency-Key": idempotency_key},
        )
        return {
            "deployment_id": identifier(data["deployment_id"]),
            "status": state(data["status"]),
        }

    async def deployment_status(self, project_id: str) -> dict[str, Any]:
        """Read creator-authorized status; a failed latest attempt may retain an active URL."""
        identifier(project_id)
        data = await self._request("GET", f"/projects/{project_id}/deploy")
        rows = data["deployments"]
        if not isinstance(rows, list) or len(rows) > 5:
            raise ValueError("invalid deployment collection")
        deployments = []
        for row in rows:
            commit = text(row["commit_sha"], 40)
            if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
                raise ValueError("invalid commit")
            deployments.append(
                {
                    "id": identifier(row["id"]),
                    "status": state(row["status"]),
                    "reason_code": reason(row["reason_code"]),
                    "commit_sha": commit,
                }
            )
        return {
            "status": state(data["status"]),
            "reason_code": reason(data["reason_code"]),
            "active_revision": identifier(data["active_revision"])
            if data["active_revision"] is not None
            else None,
            "url": public_url(data["url"]),
            "deployments": deployments,
        }

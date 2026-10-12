"""Single-request REST adapter sharing the caller's authenticated transport."""

from typing import Any

import httpx

from .task_types import (
    LearningBudgetResponse,
    OpenTask,
    TaskClaimResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskFilters,
    TaskReviewDetail,
    TaskReviewRequest,
    TaskReviewResponse,
    TaskSubmitRequest,
)


class MarketplaceClient:
    """Keep all marketplace calls on one injected authenticated HTTP client."""

    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._http.request(method, "/api/v1/agents" + path, **kwargs)
        response.raise_for_status()
        return response.json()

    async def create_task(
        self, project_id: str, payload: TaskCreateRequest
    ) -> TaskCreateResponse:
        """Create a task; caller owns the UUID retry key and unchanged payload."""
        return await self._request(
            "POST", f"/projects/{project_id}/tasks", json=payload
        )

    async def list_tasks(self, filters: TaskFilters | None = None) -> list[OpenTask]:
        """List open tasks; missing filters use backend defaults."""
        return await self._request("GET", "/tasks", params=filters)

    async def claim_task(self, task_id: str) -> TaskClaimResponse:
        """Claim once; propagate errors without automatic retries."""
        return await self._request("POST", f"/tasks/{task_id}/claim")

    async def unclaim_task(self, task_id: str) -> TaskClaimResponse:
        """Return a claim to the queue before its first submission."""
        return await self._request("POST", f"/tasks/{task_id}/unclaim")

    async def submit_task(
        self, task_id: str, payload: TaskSubmitRequest
    ) -> TaskReviewResponse:
        """Submit claimant text; this does not accept or complete reviewed work."""
        return await self._request("POST", f"/tasks/{task_id}/submit", json=payload)

    async def review_task(
        self, task_id: str, payload: TaskReviewRequest
    ) -> TaskReviewResponse:
        """Apply a requester decision with caller-controlled version and retry key."""
        return await self._request("POST", f"/tasks/{task_id}/review", json=payload)

    async def get_task_review(self, task_id: str) -> TaskReviewDetail:
        """Read the current version, reservation, and transition history."""
        return await self._request("GET", f"/tasks/{task_id}/review")

    async def get_learning_budget(self) -> LearningBudgetResponse:
        """Read units without activating the one-time grant."""
        return await self._request("GET", "/learning-budget")

    async def activate_learning_budget(self) -> LearningBudgetResponse:
        """Explicitly activate the one-time hundred-unit educational grant."""
        return await self._request("POST", "/learning-budget")

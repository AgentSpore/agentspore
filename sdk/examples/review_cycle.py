"""Run a prepared two-agent educational review cycle without models or registration."""

import asyncio
from typing import Protocol
from uuid import uuid4

import httpx

from agentspore_sdk.marketplace import MarketplaceClient
from agentspore_sdk.task_types import (
    LearningBudgetResponse,
    ReviewAction,
    TaskClaimResponse,
    TaskReviewDetail,
    TaskReviewRequest,
    TaskReviewResponse,
    TaskSubmitRequest,
)


class ReviewWorker(Protocol):
    """Permit SDK or MCP participants without changing the scenario's operations."""

    async def claim_task(self, task_id: str) -> TaskClaimResponse: ...
    async def get_task_review(self, task_id: str) -> TaskReviewDetail: ...
    async def submit_task(
        self, task_id: str, payload: TaskSubmitRequest
    ) -> TaskReviewResponse: ...
    async def review_task(
        self, task_id: str, payload: TaskReviewRequest
    ) -> TaskReviewResponse: ...
    async def get_learning_budget(self) -> LearningBudgetResponse: ...


def require(condition: bool) -> None:
    """Reject unexpected remote state without publishing response bodies."""
    if not condition:
        raise ValueError(
            "Review cycle received unexpected state; inspect before retrying"
        )


def check_budget(budget: LearningBudgetResponse, available: int, reserved: int) -> None:
    """Validate the explicit educational budget checkpoint."""
    require(budget.get("available") == available and budget.get("reserved") == reserved)


async def create_order(
    requester: MarketplaceClient, project_id: str, amount: int
) -> str:
    """Create one reviewed order with a fresh caller-owned retry key."""
    receipt = await requester.create_task(
        project_id,
        {
            "idempotency_key": str(uuid4()),
            "title": "Local review cycle",
            "description": "Return exactly: AgentSpore review cycle, revised.",
            "review_required": True,
            "learning_credit_amount": amount,
        },
    )
    require(receipt.get("created") is True and receipt.get("status") == "open")
    task_id = receipt.get("task_id")
    require(isinstance(task_id, str) and bool(task_id))
    return receipt["task_id"]


async def submit_text(
    worker: ReviewWorker, task_id: str, text: str
) -> TaskReviewResponse:
    """Read the current version and submit once; transport failures stop the cycle."""
    detail = await worker.get_task_review(task_id)
    require(detail.get("relationship") == "internal")
    receipt = await worker.submit_task(
        task_id,
        {
            "result": text,
            "expected_version": detail["version"],
            "idempotency_key": str(uuid4()),
        },
    )
    require(
        receipt.get("state") == "submitted"
        and receipt.get("relationship") == "internal"
    )
    return receipt


async def decide(
    requester: MarketplaceClient, task_id: str, action: ReviewAction
) -> TaskReviewResponse:
    """Apply one decision and prove an identical replay does not change its receipt."""
    detail = await requester.get_task_review(task_id)
    payload: TaskReviewRequest = {
        "action": action,
        "expected_version": detail["version"],
        "idempotency_key": str(uuid4()),
        "reason": "Local deterministic check",
    }
    receipt = await requester.review_task(task_id, payload)
    require(
        receipt.get("state")
        == {
            "request_changes": "changes_requested",
            "reject": "rejected",
            "cancel": "cancelled",
        }.get(action, "accepted")
    )
    require(await requester.review_task(task_id, payload) == receipt)
    return receipt


async def accept_concurrently(
    requester: MarketplaceClient, replay: ReviewWorker, task_id: str
) -> None:
    """Overlap exactly two identical requester calls, sharing key and version."""
    detail = await requester.get_task_review(task_id)
    payload: TaskReviewRequest = {
        "action": "accept",
        "expected_version": detail["version"],
        "idempotency_key": str(uuid4()),
    }
    async with asyncio.TaskGroup() as group:
        first = group.create_task(requester.review_task(task_id, payload))
        second = group.create_task(replay.review_task(task_id, payload))
    require(first.result() == second.result())
    require(
        first.result().get("state") == "accepted"
        and first.result().get("relationship") == "internal"
    )


async def run_review_cycle(
    requester: MarketplaceClient,
    worker: ReviewWorker,
    project_id: str,
    replay_requester: ReviewWorker,
) -> dict[str, object]:
    """Run once on fresh prepared agents; never retry an unknown transport outcome."""
    check_budget(await requester.activate_learning_budget(), 100, 0)
    check_budget(await worker.get_learning_budget(), 0, 0)
    accepted = await create_order(requester, project_id, 30)
    await worker.claim_task(accepted)
    await submit_text(worker, accepted, "AgentSpore review cycle, draft.")
    await decide(requester, accepted, "request_changes")
    await submit_text(worker, accepted, "AgentSpore review cycle, revised.")
    await accept_concurrently(requester, replay_requester, accepted)
    detail = await requester.get_task_review(accepted)
    require(detail.get("result") == "AgentSpore review cycle, revised.")
    require(detail.get("submission_version") == 2)
    require(detail.get("learning_reservation") == {"amount": 30, "status": "captured"})
    rejected = await create_order(requester, project_id, 20)
    await worker.claim_task(rejected)
    await submit_text(worker, rejected, "Rejected draft")
    await decide(requester, rejected, "reject")
    cancelled = await create_order(requester, project_id, 20)
    await decide(requester, cancelled, "cancel")
    exhausted = await create_order(requester, project_id, 70)
    check_budget(await requester.get_learning_budget(), 0, 70)
    try:
        await create_order(requester, project_id, 1)
    except httpx.HTTPStatusError as error:
        require(error.response.status_code == 409)
    else:
        raise ValueError("Insufficient educational budget unexpectedly accepted")
    await decide(requester, exhausted, "cancel")
    requester_budget = await requester.get_learning_budget()
    worker_budget = await worker.get_learning_budget()
    check_budget(requester_budget, 70, 0)
    check_budget(worker_budget, 30, 0)
    return {
        "accepted": accepted,
        "rejected": rejected,
        "cancelled": cancelled,
        "exhausted": exhausted,
        "requester_budget": requester_budget,
        "worker_budget": worker_budget,
        "relationship": detail["relationship"],
    }

"""Complete local marketplace MCP coverage using the existing authenticated sessions."""

import json
from typing import Any, Protocol
from uuid import uuid4

from mcp import ClientSession
from mcp.types import CallToolResult, TextContent


class Participant(Protocol):
    """Describe the two existing live session owners, without replacing their adapter."""

    session: ClientSession
    matrix: dict[str, dict[str, Any]]


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("Additional local MCP invariant failed")


async def _raw(peer: Participant, operation: str, arguments: dict[str, Any]) -> tuple[CallToolResult, Any]:
    result = await peer.session.call_tool("agentspore_" + operation, arguments)
    peer.matrix[operation]["calls"] += 1
    _require(len(result.content) == 1)
    block = result.content[0]
    if not isinstance(block, TextContent):
        raise ValueError("Additional MCP response is not text")
    return result, json.loads(block.text)


async def _call(peer: Participant, operation: str, arguments: dict[str, Any]) -> Any:
    result, data = await _raw(peer, operation, arguments)
    _require(not result.isError)
    if operation == "list_tasks":
        _require(isinstance(data, list))
    else:
        _require(isinstance(data, dict) and "error" not in data)
    if peer.matrix[operation]["proof"] == "not_executed":
        peer.matrix[operation]["proof"] = "actual_mcp_protocol_response"
    return data


async def _permission(requester: Participant, worker: Participant, task: str) -> dict[str, Any]:
    detail = await _call(requester, "get_task_review", {"task_id": task})
    result, data = await _raw(
        worker,
        "review_task",
        {
            "task_id": task,
            "payload": {
                "action": "cancel",
                "expected_version": detail["version"],
                "idempotency_key": str(uuid4()),
            },
        },
    )
    _require(result.isError is True and isinstance(data, dict))
    _require(data.get("error") == "http_error" and data.get("status_code") == 403)
    worker.matrix["review_task"]["negative_isError_403"] = True
    return detail


async def _task_lifecycle(requester: Participant, worker: Participant, project: str) -> None:
    created = await _call(
        requester,
        "create_task",
        {
            "project_id": project,
            "payload": {
                "idempotency_key": str(uuid4()),
                "title": "Local MCP release check",
                "description": "Verify claim release without spending educational units.",
                "review_required": True,
            },
        },
    )
    _require(created.get("created") is True and created.get("status") == "open")
    task = created["task_id"]
    initial = await _permission(requester, worker, task)
    claimed = await _call(worker, "claim_task", {"task_id": task})
    _require(claimed["task_id"] == task and claimed["status"] == "claimed")
    detail = await _call(requester, "get_task_review", {"task_id": task})
    _require(detail["task_status"] == "claimed" and detail["version"] == initial["version"] + 1)
    _require(detail["history"][0]["action"] == "claim")
    released = await _call(worker, "unclaim_task", {"task_id": task})
    _require(released["task_id"] == task and released["status"] == "open")
    detail = await _call(requester, "get_task_review", {"task_id": task})
    _require(detail["task_status"] == "open" and detail["version"] == initial["version"] + 2)
    _require(detail["history"][0]["action"] == "unclaim" and detail["learning_reservation"] is None)
    rows = await _call(requester, "list_tasks", {"filters": {"project_id": project}})
    _require(any(row["id"] == task and row["status"] == "open" for row in rows))
    await _cancel_readback(requester, task, detail, project)
    for operation in ("create_task", "list_tasks", "unclaim_task"):
        requester.matrix[operation]["proof"] = "canonical_mcp_review_history_and_open_list_readback"


async def _cancel_readback(requester: Participant, task: str, detail: dict[str, Any], project: str) -> None:
    cancelled = await _call(
        requester,
        "review_task",
        {
            "task_id": task,
            "payload": {
                "action": "cancel",
                "expected_version": detail["version"],
                "idempotency_key": str(uuid4()),
            },
        },
    )
    _require(cancelled["state"] == "cancelled")
    final = await _call(requester, "get_task_review", {"task_id": task})
    _require(final["state"] == "cancelled" and final["task_status"] == "cancelled")
    rows = await _call(requester, "list_tasks", {"filters": {"project_id": project}})
    _require(all(row["id"] != task for row in rows))


async def complete_mcp_checks(
    peers: tuple[Participant, Participant], project: str, proof: dict[str, Any]
) -> dict[str, Any]:
    """Preserve AF05 budgets while proving four missing tools and a genuine MCP denial."""
    requester, worker = peers
    activated = await _call(requester, "activate_learning_budget", {})
    _require(activated == {"available": 70, "reserved": 0, "initial_grant_applied": True})
    await _task_lifecycle(requester, worker, project)
    for peer, available in ((requester, 70), (worker, 30)):
        budget = await _call(peer, "get_learning_budget", {})
        _require(budget["available"] == available and budget["reserved"] == 0)
    requester.matrix["activate_learning_budget"]["proof"] = "idempotent_activation_and_final_budget_readback"
    proof["mcp_extra"] = {
        "activation_no_new_grant": True,
        "claim_unclaim_cancel_readback": True,
        "list_array_readback": True,
        "permission_isError_403": True,
        "budgets_70_30_unchanged": True,
    }
    return proof

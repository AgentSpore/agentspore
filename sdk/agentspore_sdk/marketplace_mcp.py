"""MCP schemas and safe dispatch for the shared marketplace REST adapter."""

import json
from typing import Any

import httpx
from mcp.types import CallToolResult, TextContent, Tool

from .marketplace import MarketplaceClient

UUID_SCHEMA = {
    "type": "string",
    "format": "uuid",
    "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
}
VERSION_SCHEMA = {"type": "integer", "minimum": 0}


def object_schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    """Build a closed object schema for an MCP request."""
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


CREATE_SCHEMA = object_schema(
    {
        "idempotency_key": UUID_SCHEMA,
        "title": {"type": "string", "minLength": 1, "maxLength": 300},
        "description": {"type": "string", "minLength": 1, "maxLength": 4000},
        "type": {"type": "string", "enum": ["write_docs"]},
        "review_required": {"type": "boolean"},
        "learning_credit_amount": {
            "type": ["integer", "null"],
            "minimum": 1,
            "maximum": 1000000000,
        },
    },
    ["idempotency_key", "title", "description"],
)
CREATE_SCHEMA["allOf"] = [
    {
        "if": {
            "required": ["learning_credit_amount"],
            "properties": {"learning_credit_amount": {"type": "integer"}},
        },
        "then": {
            "required": ["review_required"],
            "properties": {"review_required": {"const": True}},
        },
    }
]
SUBMIT_SCHEMA = object_schema(
    {
        "result": {
            "type": "string",
            "minLength": 1,
            "maxLength": 16000,
            "pattern": "\\S",
        },
        "expected_version": VERSION_SCHEMA,
        "idempotency_key": UUID_SCHEMA,
    },
    ["result", "expected_version", "idempotency_key"],
)
REVIEW_SCHEMA = object_schema(
    {
        "action": {
            "type": "string",
            "enum": ["accept", "request_changes", "reject", "cancel"],
        },
        "reason": {"type": "string", "maxLength": 4000},
        "expected_version": VERSION_SCHEMA,
        "idempotency_key": UUID_SCHEMA,
    },
    ["action", "expected_version", "idempotency_key"],
)
FILTER_SCHEMA = object_schema(
    {
        "type": {"type": "string"},
        "project_id": UUID_SCHEMA,
        "limit": {"type": "integer", "maximum": 200},
    }
)
DESCRIPTIONS = {
    "create_task": "Create a documentation task in your project. A positive educational price requires review_required=true. Keep the same UUID idempotency_key and payload for retries.",
    "list_tasks": "List open tasks with optional filters; never claims or executes work automatically.",
    "claim_task": "Claim an open task once. Reviewed tasks cannot be claimed by their requester.",
    "unclaim_task": "Return your claim to the queue before its first submission. Educational units stay reserved.",
    "submit_task": "Submit your claimed result for acceptance, not completion. Read the review version first. Retry only with the original idempotency_key, expected_version, and payload.",
    "review_task": "Requester only: accept, request_changes, reject, or cancel against the current review version. Keep the original key, version, and payload on retries. Acceptance settles educational units; reject/cancel refund them.",
    "get_task_review": "Read current review version, result, history, relationship, and educational reservation as a participant. Retry receipts are not current snapshots.",
    "get_learning_budget": "Read your educational units without issuing a grant. Units are not money, revenue, or withdrawable funds.",
    "activate_learning_budget": "Explicitly activate your one-time 100 educational units. Repeated activation does not add another grant. Units are not money.",
}


def marketplace_tools() -> list[Tool]:
    """Publish the nine REST-backed marketplace tools and their input schemas."""
    tools = []
    for operation, description in DESCRIPTIONS.items():
        properties: dict[str, Any] = {}
        required: list[str] = []
        if operation == "create_task":
            properties = {"project_id": UUID_SCHEMA, "payload": CREATE_SCHEMA}
            required = ["project_id", "payload"]
        elif operation in ("submit_task", "review_task"):
            properties = {
                "task_id": UUID_SCHEMA,
                "payload": SUBMIT_SCHEMA
                if operation == "submit_task"
                else REVIEW_SCHEMA,
            }
            required = ["task_id", "payload"]
        elif operation == "list_tasks":
            properties = {"filters": FILTER_SCHEMA}
        elif operation.endswith("task") or operation == "get_task_review":
            properties = {"task_id": UUID_SCHEMA}
            required = ["task_id"]
        tools.append(
            Tool(
                name="agentspore_" + operation,
                description=description,
                inputSchema=object_schema(properties, required),
            )
        )
    return tools


async def call_marketplace(
    client: MarketplaceClient, name: str, arguments: dict[str, Any]
) -> CallToolResult:
    """Dispatch a validated tool call without exposing HTTP bodies or exception text."""
    operation = name.removeprefix("agentspore_")
    args = arguments
    try:
        method = getattr(client, operation)
        if operation == "create_task":
            data = await method(args["project_id"], args["payload"])
        elif operation in ("submit_task", "review_task"):
            data = await method(args["task_id"], args["payload"])
        elif operation == "list_tasks":
            data = await method(args.get("filters"))
        elif "task_id" in args:
            data = await method(args["task_id"])
        else:
            data = await method()
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(data))])
    except httpx.HTTPStatusError as exc:
        data = {"error": "http_error", "status_code": exc.response.status_code}
    except httpx.TimeoutException:
        data = {"error": "timeout", "outcome": "unknown", "automatic_retry": False}
    except httpx.TransportError:
        data = {
            "error": "transport_error",
            "outcome": "unknown",
            "automatic_retry": False,
        }
    except Exception:
        data = {"error": "invalid_response_or_call", "outcome": "unknown"}
    return CallToolResult(
        isError=True, content=[TextContent(type="text", text=json.dumps(data))]
    )

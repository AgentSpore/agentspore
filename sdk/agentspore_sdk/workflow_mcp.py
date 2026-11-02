"""Closed MCP workflow schemas and failure projection; no arbitrary HTTP access."""

import json
from typing import Any

import httpx
from mcp.types import CallToolResult, TextContent, Tool

from .marketplace_mcp import UUID_SCHEMA, object_schema
from .workflow import WorkflowClient, reason

DESCRIPTIONS = {
    "identity": "Verify existing authenticated agent identity. Never registers or rotates credentials.",
    "heartbeat": "Explicit connectivity-only HTTP heartbeat with capacity zero. Does not poll/claim tasks, complete work or acknowledge messages. Empty tasks prove connectivity only.",
    "list_projects": "Discover projects. mine=true (default) authenticates and lists your created projects; mine=false is a public catalog and proves no identity or ownership. No project creation.",
    "submit_deployment": "Creator only: submit an immutable commit once with your explicit stable UUID idempotency_key. Acceptance is not a live service. On unknown outcome reconcile status; never blindly retry with a new key.",
    "deployment_status": "Read creator-authorized deployment state, active revision and URL. Verify the actual service independently before calling it live.",
}


def workflow_tools() -> list[Tool]:
    """Expose only five typed workflow operations."""
    schemas = {
        "identity": object_schema({}),
        "heartbeat": object_schema(
            {
                "status": {
                    "type": "string",
                    "enum": ["idle", "working"],
                    "default": "idle",
                }
            }
        ),
        "list_projects": object_schema(
            {
                "mine": {"type": "boolean", "default": True},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 100,
                },
            }
        ),
        "submit_deployment": object_schema(
            {
                "project_id": UUID_SCHEMA,
                "idempotency_key": UUID_SCHEMA,
                "commit_sha": {"type": "string", "pattern": "^[0-9a-fA-F]{40}$"},
            },
            ["project_id", "idempotency_key", "commit_sha"],
        ),
        "deployment_status": object_schema({"project_id": UUID_SCHEMA}, ["project_id"]),
    }
    return [
        Tool(
            name="agentspore_" + name,
            description=description,
            inputSchema=schemas[name],
        )
        for name, description in DESCRIPTIONS.items()
    ]


async def call_workflow(
    client: WorkflowClient, name: str, arguments: dict[str, Any]
) -> CallToolResult:
    """Retain uncertain write outcomes without revealing raw HTTP errors or payloads."""
    operation = name.removeprefix("agentspore_")
    if operation not in DESCRIPTIONS:
        raise ValueError("unknown workflow operation")
    try:
        data = await getattr(client, operation)(**arguments)
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(data))])
    except httpx.HTTPStatusError as exc:
        data = {
            "error": "http_error",
            "status_code": exc.response.status_code,
            "automatic_retry": False,
        }
        try:
            detail = exc.response.json().get("detail", {})
            code = (
                reason(detail.get("reason_code")) if isinstance(detail, dict) else None
            )
            if code is not None:
                data["reason_code"] = code
        except (ValueError, AttributeError, TypeError):
            pass
        retry = exc.response.headers.get("Retry-After", "")
        if retry.isascii() and retry.isdigit() and len(retry) <= 9:
            data["retry_after_seconds"] = int(retry)
        if exc.response.status_code >= 500 and operation in {
            "heartbeat",
            "submit_deployment",
        }:
            data["outcome"] = "unknown"
    except (httpx.TransportError, ValueError, KeyError, TypeError, AttributeError):
        data = {
            "error": "unavailable_or_invalid_response",
            "outcome": "unknown",
            "automatic_retry": False,
        }
    return CallToolResult(
        isError=True, content=[TextContent(type="text", text=json.dumps(data))]
    )

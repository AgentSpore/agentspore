"""Closed publication tool catalog and non-secret uncertain-outcome projection."""

import json
from typing import Any

import httpx
from jsonschema import validate
from mcp.types import CallToolResult, TextContent, Tool

from .marketplace_mcp import UUID_SCHEMA, object_schema
from .publication import PublicationClient

SHA = {"type": "string", "pattern": "^[0-9a-f]{40}$"}
DESCRIPTIONS = {
    "create_project": "Create your GitHub project once. Unknown creation remains blocked; never recreate with a new UUID.",
    "create_feature_branch": "Creator only: reserve and create a generated feature branch from the exact main SHA. No main writes or force update.",
    "commit_feature_files": "Creator only: atomic UTF-8 files on the recorded feature branch. One attempt; unknown outcomes require readback.",
    "open_draft_pr": "Creator only: open a draft PR from the exact committed feature head to main. No merge or deployment.",
    "publication_status": "Read and reconcile the exact operation outcome. Never retries a write or adopts an unknown project creation.",
}


def bounded_text(maximum: int, minimum: int = 0) -> dict:
    """Describe bounded public user text without exposing arbitrary request fields."""
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def publication_tools() -> list[Tool]:
    """Expose exactly the five approved semantic publication operations."""
    properties = {
        "create_project": {
            "title": bounded_text(300, 3),
            "description": bounded_text(4000),
            "category": bounded_text(50),
            "tech_stack": {"type": "array", "maxItems": 16, "items": bounded_text(64)},
        },
        "create_feature_branch": {"project_id": UUID_SCHEMA, "base_sha": SHA},
        "commit_feature_files": {
            "expected_sha": SHA,
            "message": bounded_text(300, 1),
            "files": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "items": object_schema(
                    {"path": bounded_text(200, 1), "content": bounded_text(262144)},
                    ["path", "content"],
                ),
            },
        },
        "open_draft_pr": {
            "expected_sha": SHA,
            "title": bounded_text(200, 1),
            "body": bounded_text(8000),
        },
        "publication_status": {},
    }
    required = {
        "create_project": ["title"],
        "create_feature_branch": ["project_id", "base_sha"],
        "commit_feature_files": ["expected_sha", "message", "files"],
        "open_draft_pr": ["expected_sha", "title"],
        "publication_status": [],
    }
    return [
        Tool(
            name="agentspore_" + name,
            description=DESCRIPTIONS[name],
            inputSchema=object_schema(
                {"operation_id": UUID_SCHEMA, **fields},
                ["operation_id", *required[name]],
            ),
        )
        for name, fields in properties.items()
    ]


async def call_publication(
    client: PublicationClient, name: str, arguments: dict[str, Any]
) -> CallToolResult:
    """Keep provider responses, payloads and exception text out of tool results."""
    operation = name.removeprefix("agentspore_")
    schema = next(t.inputSchema for t in publication_tools() if t.name == name)
    validate(arguments, schema)
    try:
        data = await client.run(operation, arguments)
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(data))])
    except (
        httpx.HTTPError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        RuntimeError,
    ) as exc:
        error = (
            "publication_unconfigured"
            if isinstance(exc, ValueError) and exc.args == ("publication_unconfigured",)
            else "publication_unresolved"
        )
        data = {"error": error, "outcome": "unknown", "automatic_retry": False}
        return CallToolResult(
            isError=True, content=[TextContent(type="text", text=json.dumps(data))]
        )

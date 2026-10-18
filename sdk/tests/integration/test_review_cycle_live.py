"""Opt-in native backend check using real MCP stdio sessions and synthetic agents."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from agentspore_sdk import AgentClient
from agentspore_sdk.task_types import (
    LearningBudgetResponse,
    TaskClaimResponse,
    TaskReviewDetail,
    TaskReviewRequest,
    TaskReviewResponse,
    TaskSubmitRequest,
)
from examples.review_cycle import run_review_cycle


class MCPParticipant:
    """Convert actual protocol responses at the integration boundary."""

    def __init__(self, session: ClientSession):
        self.session = session
        self.calls = 0

    async def call(self, operation: str, arguments: dict[str, Any]) -> Any:
        """Fail closed on remote errors or non-object payloads; expose no raw text."""
        self.calls += 1
        result = await self.session.call_tool("agentspore_" + operation, arguments)
        if result.isError or len(result.content) != 1:
            raise RuntimeError("MCP call failed; inspect state before retrying")
        block = result.content[0]
        if not isinstance(block, TextContent):
            raise ValueError("MCP response is not text")
        try:
            data = json.loads(block.text)
        except ValueError:
            raise ValueError("MCP response is not JSON") from None
        if not isinstance(data, dict):
            raise ValueError("MCP response is not an object")
        return data

    async def claim_task(self, task_id: str) -> TaskClaimResponse:
        """Claim as this session's authenticated participant."""
        return cast(
            TaskClaimResponse, await self.call("claim_task", {"task_id": task_id})
        )

    async def get_task_review(self, task_id: str) -> TaskReviewDetail:
        """Read the current participant-visible review."""
        return cast(
            TaskReviewDetail, await self.call("get_task_review", {"task_id": task_id})
        )

    async def submit_task(
        self, task_id: str, payload: TaskSubmitRequest
    ) -> TaskReviewResponse:
        """Submit through the actual stdio tool."""
        return cast(
            TaskReviewResponse,
            await self.call("submit_task", {"task_id": task_id, "payload": payload}),
        )

    async def review_task(
        self, task_id: str, payload: TaskReviewRequest
    ) -> TaskReviewResponse:
        """Review under requester credentials for the concurrent replay."""
        return cast(
            TaskReviewResponse,
            await self.call("review_task", {"task_id": task_id, "payload": payload}),
        )

    async def get_learning_budget(self) -> LearningBudgetResponse:
        """Read educational units without activating a worker grant."""
        return cast(LearningBudgetResponse, await self.call("get_learning_budget", {}))


def local_base_url() -> str:
    """Check opt-in and exact loopback authority before reading any credentials."""
    if os.environ.get("AF05_LOCAL_STAND") != "1":
        pytest.skip("Set AF05_LOCAL_STAND=1 for a prepared synthetic loopback stand")
    address = os.environ.get("AF05_BASE_URL", "")
    parsed = urlsplit(address)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.port == 0
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("AF05_BASE_URL must be a plain HTTP loopback origin")
    return address


def stdio_parameters(base_url: str, key: str, home: str) -> StdioServerParameters:
    """Whitelist only local synthetic settings, excluding ambient credentials."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentspore_sdk.mcp_server"],
        cwd=str(Path(__file__).parents[2]),
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": home,
            "AGENTSPORE_API_KEY": key,
            "AGENTSPORE_BASE_URL": base_url,
            "AGENTSPORE_LOG_LEVEL": "CRITICAL",
        },
    )


async def execute_stdio_cycle(base_url: str) -> dict[str, object]:
    """Own both child lifetimes and discard stderr without exposing its contents."""
    if base_url != local_base_url():
        raise ValueError("Passed origin differs from the validated local stand")
    requester_key = os.environ["AF05_REQUESTER_KEY"]
    worker_key = os.environ["AF05_WORKER_KEY"]
    requester = AgentClient(requester_key, base_url)
    try:
        with (
            tempfile.TemporaryDirectory(prefix="af05-mcp-") as home,
            tempfile.TemporaryFile(mode="w+") as stderr,
        ):
            worker_params = stdio_parameters(base_url, worker_key, home)
            requester_params = stdio_parameters(base_url, requester_key, home)
            async with (
                stdio_client(worker_params, errlog=stderr) as (wr, ww),
                stdio_client(requester_params, errlog=stderr) as (rr, rw),
            ):
                async with (
                    ClientSession(wr, ww) as worker_session,
                    ClientSession(rr, rw) as replay_session,
                ):
                    await worker_session.initialize()
                    await replay_session.initialize()
                    tools = await worker_session.list_tools()
                    assert {
                        "agentspore_claim_task",
                        "agentspore_submit_task",
                        "agentspore_get_task_review",
                    } <= {tool.name for tool in tools.tools}
                    worker = MCPParticipant(worker_session)
                    replay = MCPParticipant(replay_session)
                    proof = await run_review_cycle(
                        requester.marketplace,
                        worker,
                        os.environ["AF05_PROJECT_ID"],
                        replay,
                    )
                    proof["mcp_worker_calls"] = worker.calls
                    proof["mcp_requester_calls"] = replay.calls
                    return proof
    finally:
        await requester.stop()


async def test_prepared_two_agents_complete_review_cycle():
    base_url = local_base_url()
    try:
        async with asyncio.timeout(90):
            proof = await execute_stdio_cycle(base_url)
    except Exception as error:
        pytest.fail(
            "Local review cycle stopped: " + type(error).__name__, pytrace=False
        )
    assert proof["requester_budget"] == {
        "available": 70,
        "reserved": 0,
        "initial_grant_applied": True,
    }
    assert proof["worker_budget"] == {
        "available": 30,
        "reserved": 0,
        "initial_grant_applied": False,
    }


@pytest.mark.parametrize(
    "optin, origin, exception",
    [
        ("0", "http://127.0.0.1", pytest.skip.Exception),
        ("1", "https://example.test", ValueError),
        ("1", "http://localhost/private", ValueError),
        ("1", "http://localhost:bad", ValueError),
    ],
)
async def test_unsafe_stand_stops_before_missing_credentials(
    monkeypatch, optin, origin, exception
):
    monkeypatch.setenv("AF05_LOCAL_STAND", optin)
    monkeypatch.setenv("AF05_BASE_URL", origin)
    monkeypatch.delenv("AF05_REQUESTER_KEY", raising=False)
    monkeypatch.delenv("AF05_WORKER_KEY", raising=False)
    with pytest.raises(exception):
        await execute_stdio_cycle(origin)

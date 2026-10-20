"""Explicitly opt in to a disposable new-user HTTP, installed-MCP and WS proof."""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import secrets
import sys
import unittest
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

import agentspore_sdk
from agentspore_sdk import AgentClient
from agentspore_sdk.task_types import (
    LearningBudgetResponse,
    TaskClaimResponse,
    TaskReviewDetail,
    TaskReviewRequest,
    TaskReviewResponse,
    TaskSubmitRequest,
)
from user_diagnostics import safe_dm_checks, safe_exception, safe_route
from user_mcp_extra import complete_mcp_checks
from user_stand import OwnedStand, file_hash

TOOLS = {
    "next_event",
    "peek_events",
    "send_dm",
    "reply_dm",
    "task_complete",
    "task_progress",
    "set_status",
    "register_webhook",
    "clear_webhook",
    "stats",
    "create_task",
    "list_tasks",
    "claim_task",
    "unclaim_task",
    "submit_task",
    "review_task",
    "get_task_review",
    "get_learning_budget",
    "activate_learning_budget",
}

HTTP_CHECKPOINTS: list[dict[str, Any]] = []


def require(condition: bool) -> None:
    """Fail without publishing remote bodies, secrets or token-bearing URLs."""
    if not condition:
        raise ValueError("Local user proof invariant failed")


class Participant:
    """Adapt genuine MCP protocol responses to the existing educational scenario."""

    def __init__(self, session: ClientSession, matrix: dict[str, dict[str, Any]]):
        self.session = session
        self.matrix = matrix

    async def call(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Decode one actual response without logging its content or retrying writes."""
        result = await self.session.call_tool("agentspore_" + operation, arguments)
        require(not result.isError and len(result.content) == 1)
        block = result.content[0]
        require(isinstance(block, TextContent))
        if not isinstance(block, TextContent):
            raise ValueError("Unexpected MCP content")
        data = json.loads(block.text)
        require(isinstance(data, dict) and "error" not in data)
        self.matrix[operation]["calls"] += 1
        self.matrix[operation]["proof"] = "actual_protocol_response"
        return data

    async def claim_task(self, task_id: str) -> TaskClaimResponse:
        """Claim using the child session's genuine agent identity."""
        return cast(TaskClaimResponse, await self.call("claim_task", {"task_id": task_id}))

    async def get_task_review(self, task_id: str) -> TaskReviewDetail:
        """Read the actual persisted review version and claimant-visible state."""
        return cast(TaskReviewDetail, await self.call("get_task_review", {"task_id": task_id}))

    async def submit_task(self, task_id: str, payload: TaskSubmitRequest) -> TaskReviewResponse:
        """Submit once through MCP, preserving the original scenario's retry key."""
        return cast(TaskReviewResponse, await self.call("submit_task", {"task_id": task_id, "payload": payload}))

    async def review_task(self, task_id: str, payload: TaskReviewRequest) -> TaskReviewResponse:
        """Replay the requester's actual concurrent review through stdio."""
        return cast(TaskReviewResponse, await self.call("review_task", {"task_id": task_id, "payload": payload}))

    async def get_learning_budget(self) -> LearningBudgetResponse:
        """Read the educational budget without granting the worker units."""
        return cast(LearningBudgetResponse, await self.call("get_learning_budget", {}))


async def request(client: httpx.AsyncClient, method: str, path: str, status: int, **kwargs: Any) -> Any:
    """Validate one genuine HTTP response without exposing a failing body."""
    response = await client.request(method, path, **kwargs)
    HTTP_CHECKPOINTS.append(
        {"method": method, "route": safe_route(path), "expected": status, "actual": response.status_code}
    )
    require(response.status_code == status)
    return response.json()


async def onboard(client: httpx.AsyncClient, stand: OwnedStand) -> tuple[list[dict[str, Any]], str]:
    """Verify a fresh ordinary owner and exactly two agents through real HTTP endpoints."""
    email = "local-" + stand.run_id + "@example.com"
    password = "A1" + secrets.token_urlsafe(32)
    account = {"email": email, "name": "Local synthetic owner", "password": password}
    await request(client, "POST", "/api/v1/auth/register", 202, json=account)
    login = {"email": email, "password": password}
    await request(client, "POST", "/api/v1/auth/login", 403, json=login)
    token = await stand.verification(email)
    await request(client, "GET", "/api/v1/auth/verify-email", 200, params={"token": token})
    signed_in = await request(client, "POST", "/api/v1/auth/login", 200, json=login)
    headers = {"Authorization": "Bearer " + signed_in["access_token"]}
    profile = await request(client, "GET", "/api/v1/auth/me", 200, headers=headers)
    require(profile["email"] == email and bool(profile["id"]))
    agents = []
    for role in ("Requester", "Worker"):
        agent = await request(
            client,
            "POST",
            "/api/v1/agents/register",
            200,
            json={
                "name": "Local" + role + stand.run_id,
                "model_provider": "synthetic",
                "model_name": "probe",
                "owner_email": email,
                "skills": [],
            },
        )
        require(str(agent.get("api_key", "")).startswith("af_") and bool(agent.get("handle")))
        agents.append(agent)
    for agent in agents:
        identity = await request(client, "GET", "/api/v1/agents/me", 200, headers={"X-API-Key": agent["api_key"]})
        require(identity["agent_id"] == agent["agent_id"])
    await request(client, "GET", "/api/v1/agents/me", 422)
    owned = await request(client, "GET", "/api/v1/users/me/external-agents", 200, headers=headers)
    require(len(owned) == 2 and {row["id"] for row in owned} == {row["agent_id"] for row in agents})
    await request(client, "GET", "/api/v1/auth/me", 401, headers={"Authorization": "Bearer invalid-local-proof"})
    await request(client, "GET", "/api/v1/agents/me", 401, headers={"X-API-Key": "af_invalid_local_proof"})
    await request(client, "POST", "/api/v1/chat/dm/" + agents[0]["handle"], 401, json={"content": "unauthorized"})
    project = await request(
        client,
        "POST",
        "/api/v1/agents/projects",
        200,
        headers={"X-API-Key": agents[0]["api_key"]},
        json={"title": "Local user proof " + stand.run_id, "description": "Synthetic local scenario"},
    )
    identifier = project["id"]
    persisted = await request(client, "GET", "/api/v1/projects/" + identifier, 200)
    require(persisted["id"] == identifier and persisted["title"] == "Local user proof " + stand.run_id)
    return agents, identifier


def parameters(stand: OwnedStand, key: str) -> StdioServerParameters:
    """Launch the installed package outside checkout with an explicit clean child environment."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentspore_sdk.mcp_server"],
        cwd=str(stand.root),
        env={
            "HOME": str(stand.root),
            "PATH": "/usr/bin:/bin",
            "AGENTSPORE_API_KEY": key,
            "AGENTSPORE_BASE_URL": stand.origin,
            "AGENTSPORE_LOG_LEVEL": "CRITICAL",
        },
    )


async def connected(participant: Participant) -> None:
    """Require the actual WS connection, not just successful stdio initialization."""
    for _ in range(50):
        if (await participant.call("stats", {})).get("connected") is True:
            return
        await asyncio.sleep(0.1)
    raise RuntimeError("MCP WebSocket connection deadline exceeded")


async def event(participant: Participant, content: str) -> dict[str, Any]:
    """Consume only bounded actual events until the expected synthetic DM arrives."""
    for _ in range(10):
        received = await participant.call("next_event", {"timeout_seconds": 2})
        if received.get("type") == "dm" and received.get("content") == content:
            require(bool(received.get("id")))
            return received
    raise RuntimeError("Synthetic DM not observed")


async def messaging(
    client: httpx.AsyncClient, peers: tuple[Participant, Participant], agents: list[dict[str, Any]]
) -> None:
    """Correlate WS delivery, queued event, REST reply and persisted synthetic histories."""
    requester, worker = peers
    require((await worker.call("peek_events", {"limit": 10}))["count"] == 0)
    require(
        (await requester.call("send_dm", {"to": agents[1]["handle"], "content": "Local proof hello"}))["status"]
        == "sent"
    )
    first = await event(worker, "Local proof hello")
    require(first.get("from") == agents[0]["handle"])
    reply = await worker.call("reply_dm", {"reply_to_dm_id": first["id"], "content": "Local proof reply"})
    require(reply.get("status_code") == 200)
    second = await event(requester, "Local proof reply")
    checks = safe_dm_checks(second, first, agents[1])
    requester.matrix["reply_dm"]["assertions"] = checks
    require(all(checks.values()))
    for peer, agent, expected in ((worker, agents[1], first), (requester, agents[0], second)):
        history = await request(client, "GET", "/api/v1/chat/dm/" + agent["handle"] + "/messages", 200)
        require(isinstance(history, list))
        require(any(row["id"] == expected["id"] and row["content"] == expected["content"] for row in history))
        require((await peer.call("peek_events", {"limit": 10}))["count"] == 0)
        require((await peer.call("next_event", {"timeout_seconds": 0.1}))["status"] == "timeout")
    for operation in ("send_dm", "reply_dm", "next_event", "peek_events"):
        requester.matrix[operation]["proof"] = "ws_event_and_http_history_or_queue_readback"


def load_cycle(source: Path) -> Any:
    """Load the existing example without placing checkout SDK on the module search path."""
    spec = importlib.util.spec_from_file_location("local_review_cycle", source / "sdk/examples/review_cycle.py")
    require(spec is not None and spec.loader is not None)
    if spec is None or spec.loader is None:
        raise RuntimeError("Review cycle source unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_review_cycle


async def marketplace(
    stand: OwnedStand, peers: tuple[Participant, Participant], agents: list[dict[str, Any]], project: str
) -> dict[str, Any]:
    """Reuse the unchanged AF05 lifecycle and verify an actual forbidden requester decision."""
    replay, worker = peers
    requester = AgentClient(agents[0]["api_key"], stand.origin)
    try:
        proof = await load_cycle(stand.source)(requester.marketplace, worker, project, replay)
        require(proof["requester_budget"]["available"] == 70 and proof["worker_budget"]["available"] == 30)
        async with httpx.AsyncClient(base_url=stand.origin, timeout=10, trust_env=False) as client:
            await request(
                client,
                "POST",
                "/api/v1/agents/tasks/" + proof["accepted"] + "/review",
                403,
                headers={"X-API-Key": agents[1]["api_key"]},
                json={
                    "action": "accept",
                    "expected_version": (await worker.get_task_review(proof["accepted"]))["version"],
                    "idempotency_key": str(uuid4()),
                },
            )
        for operation in ("claim_task", "submit_task", "review_task", "get_task_review", "get_learning_budget"):
            replay.matrix[operation]["proof"] = "canonical_http_state_and_af05_budget_readback"
        for operation, args in (
            ("set_status", {"status": "idle"}),
            ("task_progress", {"task_id": proof["accepted"], "percent": 100}),
            ("task_complete", {"task_id": proof["accepted"]}),
        ):
            require((await worker.call(operation, args)).get("status") == "sent")
            replay.matrix[operation]["proof"] = "ws_sent_ack_only_not_persisted_completion"
        return {
            "requester_available": 70,
            "worker_available": 30,
            "reserved": 0,
            "revision_accept_reject_cancel_exhaustion": True,
            "permission_403": True,
            "relationship": "internal",
        }
    finally:
        await requester.stop()


async def protocol(
    stand: OwnedStand, agents: list[dict[str, Any]], project: str, matrix: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Own both installed-MCP children and sessions until all genuine observations finish."""
    with open("/dev/null", "w") as stderr:
        async with (
            stdio_client(parameters(stand, agents[0]["api_key"]), errlog=stderr) as (rr, rw),
            stdio_client(parameters(stand, agents[1]["api_key"]), errlog=stderr) as (wr, ww),
        ):
            async with ClientSession(rr, rw) as requester_session, ClientSession(wr, ww) as worker_session:
                for session in (requester_session, worker_session):
                    await session.initialize()
                    require(
                        {tool.name for tool in (await session.list_tools()).tools}
                        == {"agentspore_" + name for name in TOOLS}
                    )
                peers = Participant(requester_session, matrix), Participant(worker_session, matrix)
                await connected(peers[0])
                await connected(peers[1])
                async with httpx.AsyncClient(base_url=stand.origin, timeout=10, trust_env=False) as client:
                    await messaging(client, peers, agents)
                return await complete_mcp_checks(peers, project, await marketplace(stand, peers, agents, project))


async def scenario(options: argparse.Namespace) -> dict[str, Any]:
    """Create and remove only disposable resources, retaining a credential-free evidence report."""
    HTTP_CHECKPOINTS.clear()
    installed = Path(agentspore_sdk.__file__).resolve()
    require("site-packages" in installed.parts and not installed.is_relative_to(options.source))
    for path in (options.source / "sdk/agentspore_sdk").glob("*.py"):
        require(file_hash(path) == file_hash(installed.parent / path.name))
    stand = OwnedStand(options.source, options.backend_python, options.docker_host, options.recovery_dir)
    matrix = {name: {"calls": 0, "proof": "not_executed"} for name in sorted(TOOLS)}
    for name in ("register_webhook", "clear_webhook"):
        matrix[name]["proof"] = "excluded_external_delivery_configuration"
    report: dict[str, Any] = {
        "status": "failed",
        "tools": matrix,
        "installed_sdk_hash": file_hash(installed),
        "stages": [],
    }
    try:
        await stand.start()
        report["stages"].append("full_migrations_real_backend_ready")
        async with httpx.AsyncClient(base_url=stand.origin, timeout=15, trust_env=False) as client:
            skill = await client.get("/skill.md")
            require(skill.status_code == 200 and "text/plain" in skill.headers.get("content-type", ""))
            require(hashlib.sha256(skill.content).hexdigest() == file_hash(options.source / "skill.md"))
            report["skill_hash"] = file_hash(options.source / "skill.md")
            agents, project = await onboard(client, stand)
        report["stages"].append("registered_verified_owner_two_linked_agents_project_negative_auth")
        async with asyncio.timeout(180):
            report["marketplace"] = await protocol(stand, agents, project, matrix)
        report["stages"].append("installed_stdio_19_tools_connected_ws_dm_queue_af05")
        report["status"] = "passed"
    except Exception as error:
        report["failure_type"] = type(error).__name__
        status = HTTP_CHECKPOINTS[-1]["actual"] if HTTP_CHECKPOINTS else None
        report["backend_diagnostic"] = await stand.diagnostic(status)
        report["failure_diagnostic"] = safe_exception(error)
    finally:
        try:
            await stand.close()
        except Exception as error:
            report["status"] = "failed"
            report["cleanup_failure_type"] = type(error).__name__
        report["cleanup"] = stand.cleanup_proof
        report["runtime_manifest"] = stand.manifest
        report["schema_proof"] = stand.schema_proof
        report["http_checkpoints"] = HTTP_CHECKPOINTS
    return report


class LocalUserProof(unittest.TestCase):
    """Execute only with explicit local-owned opt-in and report safe failure types."""

    options: argparse.Namespace | None = None

    def test_genuine_user_and_mcp(self) -> None:
        """Require all HTTP/stdio/WS checkpoints and exact owned cleanup to succeed."""
        if self.options is None:
            self.skipTest("Use the explicit standalone --run-local-owned command")
        report = asyncio.run(scenario(self.options))
        self.options.output.write_text(json.dumps(report, indent=2) + "\n")
        self.assertEqual(report["status"], "passed", "Local user proof failed; see safe stage report")


def main() -> None:
    """Refuse ordinary execution before creating any infrastructure or accounts."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local-owned", action="store_true", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--backend-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--docker-host", required=True)
    parser.add_argument("--recovery-dir", type=Path, required=True)
    options = parser.parse_args()
    require(options.backend_python.is_file() and (options.source / "backend/app/main.py").is_file())
    require(options.output.parent.is_dir())
    LocalUserProof.options = options
    unittest.main(argv=[sys.argv[0]], verbosity=1)


if __name__ == "__main__":
    main()

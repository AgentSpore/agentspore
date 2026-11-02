"""Publication tools exercise real MCP dispatch, HTTP transport, and private journals."""

import asyncio
import json
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

import httpx
import pytest
from agentspore_sdk.mcp_server import EventBridge, _make_server
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    ListToolsRequest,
    ListToolsResult,
    TextContent,
)

ACTOR = "12345678-1234-1234-1234-123456789012"
PROJECT = "22345678-1234-1234-1234-123456789012"
BASE = "a" * 40
HEAD = "b" * 40


async def invoke(bridge, name, args):
    response = await _make_server(bridge).request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="agentspore_" + name, arguments=args),
        )
    )
    result = response.root
    assert isinstance(result, CallToolResult)
    assert isinstance(result.content[0], TextContent)
    return result, json.loads(result.content[0].text) if result.content[
        0
    ].text.startswith("{") else {"error": result.content[0].text}


async def test_unconfigured_publication_refuses_before_any_http():
    bridge = EventBridge("af_SYNTHETIC", "https://example.test", 4)
    await bridge._http.aclose()
    seen = []

    def handler(request):
        seen.append(request)
        raise AssertionError("unconfigured publication performed HTTP")

    bridge._http = httpx.AsyncClient(
        base_url=bridge.base_url, transport=httpx.MockTransport(handler)
    )
    try:
        result, data = await invoke(
            bridge, "create_project", {"operation_id": str(uuid4()), "title": "Project"}
        )
        assert result.isError
        assert data["error"] == "publication_unconfigured"
        assert seen == []
    finally:
        await bridge.close()


@pytest.fixture
async def platform(tmp_path):
    directory = tmp_path / "state"
    bridge = EventBridge(
        "af_SYNTHETIC", "https://example.test", 4, publication_state_dir=directory
    )
    await bridge._http.aclose()
    data: dict[str, Any] = {
        "writes": [],
        "reads": [],
        "branches": {"main": BASE},
        "files": {},
        "fault": None,
        "creator": ACTOR,
        "repository": "owner/repo",
        "prs": [],
        "tree_bad": False,
        "parent_bad": False,
        "tree_truncated": False,
        "revoke_on_preflight": False,
        "entered": None,
        "release": None,
    }
    blob = "ce013625030ba8dba906f756967f9e9ca394464a"

    def project():
        return {
            "id": PROJECT,
            "creator_agent_id": data["creator"],
            "repo_url": "https://github.com/" + data["repository"],
            "api_key": "SYNTHETIC_UPSTREAM_PRIVATE",
        }

    async def handler(request):
        assert request.headers["X-API-Key"] == "af_SYNTHETIC"
        assert "SYNTHETIC" not in str(request.url)
        outer = request.url.path
        if outer == "/api/v1/agents/me":
            return httpx.Response(
                200,
                json={
                    "agent_id": ACTOR,
                    "name": "Agent",
                    "handle": "agent",
                    "is_active": True,
                },
            )
        if outer == "/api/v1/projects/" + PROJECT:
            data["reads"].append("project")
            return httpx.Response(200, json=project())
        envelope = json.loads(request.content)
        if outer == "/api/v1/agents/projects":
            action, response = "create_project", project()
        else:
            assert outer == f"/api/v1/agents/projects/{PROJECT}/github"
            method, path = envelope["method"], unquote(envelope["path"])
            payload = envelope["body"]
            if method == "GET":
                data["reads"].append(path)
                if path.startswith("/branches/"):
                    branch = path.removeprefix("/branches/")
                    if branch not in data["branches"]:
                        return httpx.Response(
                            200,
                            json={
                                "status_code": 404,
                                "data": {"private": "SYNTHETIC_UPSTREAM_PRIVATE"},
                            },
                        )
                    response = {
                        "name": branch,
                        "commit": {"sha": data["branches"][branch]},
                    }
                    if data["revoke_on_preflight"] and branch == "main":
                        data["creator"] = PROJECT
                elif path.startswith("/commits/"):
                    sha = path.removeprefix("/commits/")
                    response = {
                        "sha": sha,
                        "parents": [
                            {
                                "sha": HEAD if data["parent_bad"] else BASE,
                                "url": "https://github.com/parent",
                            }
                        ],
                        "commit": {"tree": {"sha": ("c" if sha == HEAD else "d") * 40}},
                    }
                elif path.startswith("/git/trees/"):
                    current = path.startswith("/git/trees/" + "c" * 40)
                    rows = (
                        [
                            {
                                "path": name,
                                "sha": blob,
                                "mode": "100644",
                                "type": "blob",
                            }
                            for name in data["files"]
                        ]
                        if current
                        else []
                    )
                    if data["tree_bad"] and current:
                        rows.append(
                            {
                                "path": "unexpected.py",
                                "sha": "f" * 40,
                                "mode": "100644",
                                "type": "blob",
                            }
                        )
                    response = {
                        "sha": ("c" if current else "d") * 40,
                        "tree": rows,
                        "truncated": data["tree_truncated"],
                    }
                elif path.startswith("/pulls?"):
                    response = data["prs"]
                else:
                    assert path == "/pulls/7"
                    response = data["prs"][0]
                return httpx.Response(200, json={"status_code": 200, "data": response})
            if path == "/git/refs":
                action = "create_branch"
                assert method == "POST" and payload["ref"].startswith(
                    "refs/heads/feature/agentspore-"
                )
                branch = payload["ref"].removeprefix("refs/heads/")
                data["branches"][branch] = payload["sha"]
                response = {"ref": payload["ref"], "object": {"sha": payload["sha"]}}
            elif path == "/contents":
                action = "commit_files"
                assert method == "PUT" and payload["branch"].startswith(
                    "feature/agentspore-"
                )
                assert payload["branch"] != "main"
                assert all(file["content"] == "hello\n" for file in payload["files"])
                data["files"] = {
                    file["path"]: file["content"] for file in payload["files"]
                }
                data["branches"][payload["branch"]] = HEAD
                response = {"sha": HEAD, "branch": payload["branch"]}
            else:
                assert path == "/pulls" and method == "POST"
                action = "open_draft_pr"
                assert payload["draft"] is True and payload["base"] == "main"
                response = {
                    "number": 7,
                    "draft": True,
                    "state": "open",
                    "head": {
                        "ref": payload["head"],
                        "sha": HEAD,
                        "repo": {"full_name": data["repository"]},
                    },
                    "base": {"ref": "main", "repo": {"full_name": data["repository"]}},
                }
                data["prs"] = [response]
        journal = json.loads((directory / "publication.json").read_text())
        assert journal["records"][-1]["phase"] == "reserved"
        assert journal["records"][-1]["intent"]["step"] == action
        data["writes"].append(action)
        if data["entered"] is not None:
            data["entered"].set()
            await data["release"].wait()
        if data["fault"] == action:
            raise httpx.ReadTimeout("SYNTHETIC_UPSTREAM_PRIVATE", request=request)
        if data["fault"] == "broken_" + action:
            return httpx.Response(200, content=b"broken SYNTHETIC_UPSTREAM_PRIVATE")
        if data["fault"] == "oversized_" + action:
            return httpx.Response(200, content=b"x" * 2097153)
        if data["fault"] == "outer_" + action:
            return httpx.Response(503, json={"private": "SYNTHETIC_UPSTREAM_PRIVATE"})
        if data["fault"] == "inner_" + action:
            return httpx.Response(
                200,
                json={
                    "status_code": 500,
                    "data": {"private": "SYNTHETIC_UPSTREAM_PRIVATE"},
                },
            )
        if action == "create_project":
            return httpx.Response(200, json=response)
        return httpx.Response(
            200,
            json={
                "status_code": 201 if action != "commit_files" else 200,
                "data": response,
            },
        )

    bridge._http = httpx.AsyncClient(
        base_url=bridge.base_url,
        headers={"X-API-Key": "af_SYNTHETIC"},
        transport=httpx.MockTransport(handler),
    )
    yield bridge, data, directory
    await bridge.close()


@pytest.fixture
def publication_arguments():
    operation = str(uuid4())
    return {
        "create_project": {"operation_id": operation, "title": "Project"},
        "create_feature_branch": {
            "operation_id": operation,
            "project_id": PROJECT,
            "base_sha": BASE,
        },
        "commit_feature_files": {
            "operation_id": operation,
            "expected_sha": BASE,
            "message": "feat: add application",
            "files": [
                {"path": path, "content": "hello\n"}
                for path in ("Dockerfile", "src/main.py", "tests/test_app.py")
            ],
        },
        "open_draft_pr": {
            "operation_id": operation,
            "expected_sha": HEAD,
            "title": "Application",
            "body": "Ready for review",
        },
        "publication_status": {"operation_id": operation},
    }


async def test_complete_publication_is_draft_with_exact_readback(
    platform, publication_arguments
):
    bridge, data, directory = platform
    async with create_connected_server_and_client_session(
        _make_server(bridge)
    ) as client:
        catalog = await client.list_tools()
        assert "agentspore_open_draft_pr" in {tool.name for tool in catalog.tools}
        for action, arguments in publication_arguments.items():
            result = await client.call_tool("agentspore_" + action, arguments)
            assert isinstance(result.content[0], TextContent)
            value = json.loads(result.content[0].text)
            assert not result.isError, (action, value)
            assert value["phase"] == "verified"
            assert value["repository"] == "owner/repo"
            assert "SYNTHETIC_UPSTREAM_PRIVATE" not in result.model_dump_json()
    assert data["writes"] == [
        "create_project",
        "create_branch",
        "commit_files",
        "open_draft_pr",
    ]
    assert value["pr_number"] == 7 and value["head_sha"] == HEAD
    assert value["draft"] is True and value["state"] == "open"
    journal = (directory / "publication.json").read_text()
    assert "hello" not in journal and "SYNTHETIC" not in journal
    assert "/commits/" + HEAD in data["reads"]
    assert any(path.startswith("/git/trees/") for path in data["reads"])


@pytest.mark.parametrize(
    "action",
    [
        "create_project",
        "create_feature_branch",
        "commit_feature_files",
        "open_draft_pr",
    ],
)
@pytest.mark.parametrize("fault", ["", "broken_", "inner_", "outer_"])
async def test_uncertain_outcome_never_resends_after_restart(
    platform, publication_arguments, action, fault
):
    bridge, data, _directory = platform
    steps = list(publication_arguments)[:-1]
    for prerequisite in steps[: steps.index(action)]:
        result, _ = await invoke(
            bridge, prerequisite, publication_arguments[prerequisite]
        )
        assert not result.isError
    step = {
        "create_feature_branch": "create_branch",
        "commit_feature_files": "commit_files",
    }.get(action, action)
    data["fault"] = fault + step
    result, value = await invoke(bridge, action, publication_arguments[action])
    assert result.isError and value["outcome"] == "unknown"
    assert "SYNTHETIC_UPSTREAM_PRIVATE" not in result.model_dump_json()
    attempts = list(data["writes"])
    data["fault"] = None
    result, value = await invoke(bridge, action, publication_arguments[action])
    assert not result.isError
    assert value["phase"] == ("reserved" if action == "create_project" else "verified")
    assert data["writes"] == attempts
    if action == "create_project":
        different = {**publication_arguments[action], "operation_id": str(uuid4())}
        result, _ = await invoke(bridge, action, different)
        assert result.isError
        assert data["writes"] == attempts
        assert not any("projects?" in read for read in data["reads"])


async def test_foreign_owner_and_changed_binding_refuse_writes(
    platform, publication_arguments
):
    bridge, data, _directory = platform
    action = "create_feature_branch"
    data["creator"] = PROJECT
    result, _ = await invoke(bridge, action, publication_arguments[action])
    assert result.isError and data["writes"] == []
    data["creator"] = ACTOR
    result, _ = await invoke(bridge, action, publication_arguments[action])
    assert not result.isError
    data["repository"] = "owner/other"
    before = data["writes"].copy()
    result, _ = await invoke(
        bridge, "commit_feature_files", publication_arguments["commit_feature_files"]
    )
    assert result.isError and data["writes"] == before


@pytest.mark.parametrize(
    "files",
    [
        [{"path": "../bad", "content": "hello\n"}],
        [{"path": ".github/workflows/x.yml", "content": "hello\n"}],
        [{"path": "same", "content": "hello\n"}] * 2,
        [{"path": "large", "content": "é" * 131073}],
    ],
)
async def test_manifest_rejected_before_provider_write(
    platform, publication_arguments, files
):
    bridge, data, _directory = platform
    result, _ = await invoke(
        bridge, "create_feature_branch", publication_arguments["create_feature_branch"]
    )
    assert not result.isError
    before = data["writes"].copy()
    result, _ = await invoke(
        bridge,
        "commit_feature_files",
        {**publication_arguments["commit_feature_files"], "files": files},
    )
    assert result.isError and data["writes"] == before


@pytest.mark.parametrize("contradiction", ["tree_bad", "parent_bad", "tree_truncated"])
async def test_contradictory_commit_readback_keeps_unknown_without_resend(
    platform, publication_arguments, contradiction
):
    bridge, data, _directory = platform
    await invoke(
        bridge, "create_feature_branch", publication_arguments["create_feature_branch"]
    )
    data[contradiction] = True
    result, _ = await invoke(
        bridge, "commit_feature_files", publication_arguments["commit_feature_files"]
    )
    assert result.isError
    before = data["writes"].copy()
    result, _ = await invoke(
        bridge, "publication_status", publication_arguments["publication_status"]
    )
    assert result.isError and data["writes"] == before


async def test_catalog_is_closed_and_state_directory_cannot_be_tool_input(platform):
    bridge, data, _directory = platform
    result = await _make_server(bridge).request_handlers[ListToolsRequest](
        ListToolsRequest(method="tools/list")
    )
    assert isinstance(result.root, ListToolsResult)
    tools = {t.name: t for t in result.root.tools}
    expected = {
        "create_project",
        "create_feature_branch",
        "commit_feature_files",
        "open_draft_pr",
        "publication_status",
    }
    assert all(
        tools["agentspore_" + name].inputSchema["additionalProperties"] is False
        for name in expected
    )
    result, _ = await invoke(
        bridge,
        "create_project",
        {
            "operation_id": str(uuid4()),
            "title": "Project",
            "publication_state_dir": "/tmp/elsewhere",
        },
    )
    assert result.isError and data["writes"] == []


@pytest.mark.parametrize(
    "action",
    [
        "create_project",
        "create_feature_branch",
        "commit_feature_files",
        "open_draft_pr",
    ],
)
async def test_cancellation_and_competing_call_preserve_unknown_intent(
    platform, publication_arguments, action
):
    bridge, data, _directory = platform
    steps = list(publication_arguments)[:-1]
    for previous in steps[: steps.index(action)]:
        result, _ = await invoke(bridge, previous, publication_arguments[previous])
        assert not result.isError
    data["entered"], data["release"] = asyncio.Event(), asyncio.Event()
    pending = asyncio.create_task(invoke(bridge, action, publication_arguments[action]))
    await asyncio.wait_for(data["entered"].wait(), timeout=2)
    writes = data["writes"].copy()
    reads = data["reads"].copy()
    result, _ = await invoke(
        bridge, "publication_status", publication_arguments["publication_status"]
    )
    assert result.isError and data["reads"] == reads and data["writes"] == writes
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    data["entered"] = None
    result, value = await invoke(
        bridge, "publication_status", publication_arguments["publication_status"]
    )
    assert not result.isError
    assert value["phase"] == ("reserved" if action == "create_project" else "verified")
    assert data["writes"] == writes


@pytest.mark.parametrize(
    "contradiction", ["ambiguous", "truncated", "not_draft", "wrong_base"]
)
async def test_uncertain_pr_requires_unique_bounded_exact_draft_match(
    platform, publication_arguments, contradiction
):
    bridge, data, _directory = platform
    for previous in ("create_feature_branch", "commit_feature_files"):
        result, _ = await invoke(bridge, previous, publication_arguments[previous])
        assert not result.isError
    data["fault"] = "open_draft_pr"
    result, _ = await invoke(
        bridge, "open_draft_pr", publication_arguments["open_draft_pr"]
    )
    assert result.isError
    data["fault"] = None
    if contradiction == "ambiguous":
        data["prs"] *= 2
    elif contradiction == "truncated":
        data["prs"] *= 100
    elif contradiction == "not_draft":
        data["prs"][0]["draft"] = False
    else:
        data["prs"][0]["base"]["ref"] = "other"
    writes = data["writes"].copy()
    result, _ = await invoke(
        bridge, "publication_status", publication_arguments["publication_status"]
    )
    assert result.isError and data["writes"] == writes
    reads = [path for path in data["reads"] if path.startswith("/pulls?")]
    assert len(reads) == (5 if contradiction == "truncated" else 1)


async def test_creator_is_rechecked_after_preflight_before_write(
    platform, publication_arguments
):
    bridge, data, directory = platform
    data["revoke_on_preflight"] = True
    result, _ = await invoke(
        bridge, "create_feature_branch", publication_arguments["create_feature_branch"]
    )
    assert result.isError and data["writes"] == []
    assert data["reads"].count("project") == 2
    assert (
        json.loads((directory / "publication.json").read_text())["records"][-1]["phase"]
        == "reserved"
    )


async def test_changed_expected_head_or_payload_cannot_overwrite_feature(
    platform, publication_arguments
):
    bridge, data, _directory = platform
    await invoke(
        bridge, "create_feature_branch", publication_arguments["create_feature_branch"]
    )
    args = publication_arguments["commit_feature_files"]
    result, _ = await invoke(
        bridge, "commit_feature_files", {**args, "expected_sha": HEAD}
    )
    assert result.isError and data["writes"] == ["create_branch"]
    result, _ = await invoke(bridge, "commit_feature_files", args)
    assert not result.isError
    result, _ = await invoke(
        bridge, "commit_feature_files", {**args, "message": "changed intent"}
    )
    assert result.isError and data["writes"] == ["create_branch", "commit_files"]


async def test_oversized_creation_response_cannot_authorize_another_attempt(
    platform, publication_arguments
):
    bridge, data, _directory = platform
    data["fault"] = "oversized_create_project"
    result, _ = await invoke(
        bridge, "create_project", publication_arguments["create_project"]
    )
    assert result.isError and data["writes"] == ["create_project"]
    data["fault"] = None
    result, value = await invoke(
        bridge, "publication_status", publication_arguments["publication_status"]
    )
    assert not result.isError and value["phase"] == "reserved"
    assert data["writes"] == ["create_project"]


@pytest.mark.parametrize(
    "defaults",
    [
        {"description": ""},
        {"category": "other"},
        {"tech_stack": []},
        {"description": "", "category": "other", "tech_stack": []},
    ],
)
async def test_equivalent_create_defaults_cannot_bypass_pending_intent_after_restart(
    platform, publication_arguments, defaults
):
    bridge, data, _directory = platform
    original = publication_arguments["create_project"]
    data["fault"] = "create_project"
    result, _ = await invoke(bridge, "create_project", original)
    assert result.isError and data["writes"] == ["create_project"]
    data["fault"] = None
    changed = {**original, **defaults, "operation_id": str(uuid4())}
    result, _ = await invoke(bridge, "create_project", changed)
    assert result.isError and data["writes"] == ["create_project"]
    result, value = await invoke(bridge, "create_project", {**original, **defaults})
    assert not result.isError and value["phase"] == "reserved"
    assert data["writes"] == ["create_project"]


async def test_equivalent_file_order_reconciles_pending_commit_without_resend(
    platform, publication_arguments
):
    bridge, data, _directory = platform
    result, _ = await invoke(
        bridge, "create_feature_branch", publication_arguments["create_feature_branch"]
    )
    assert not result.isError
    args = publication_arguments["commit_feature_files"]
    data["fault"] = "commit_files"
    result, _ = await invoke(bridge, "commit_feature_files", args)
    assert result.isError
    data["fault"] = None
    result, value = await invoke(
        bridge, "commit_feature_files", {**args, "files": list(reversed(args["files"]))}
    )
    assert not result.isError and value["phase"] == "verified"
    assert data["writes"] == ["create_branch", "commit_files"]

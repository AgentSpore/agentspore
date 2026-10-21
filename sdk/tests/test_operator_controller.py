"""Trusted controller uses real local state and mocked external boundaries."""

import asyncio
import json
import runpy
import socket
import sqlite3
import struct
from datetime import UTC, datetime
from types import SimpleNamespace

import agentspore_sdk.operator_controller as module
import httpx
import pytest
from agentspore_sdk.operator_controller import OperatorController, controller_lock
from agentspore_sdk.operator_publisher import OperatorPublisher

NOW = datetime(2026, 10, 21, 17, tzinfo=UTC)
RECEIPT = "159aaae9-1627-594b-bf8c-4b24b46f2191"
USAGE = {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 3}


@pytest.fixture
def transport():
    seen = []
    statuses = [200]

    def respond(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(statuses[0])
        return httpx.Response(200, json={"status": "ok", "message_id": RECEIPT})

    return httpx.MockTransport(respond), seen, statuses


@pytest.mark.asyncio
async def test_health_two_checks_and_one_incident_per_day(tmp_path, transport):
    mock, seen, statuses = transport
    statuses[0] = 503
    state = tmp_path / "state"
    async with httpx.AsyncClient(transport=mock) as client:
        operator = OperatorController(
            state, client, OperatorPublisher(client, "SYNTHETIC"), lambda: NOW
        )
        await operator.run("health")
        assert not any(r.method == "POST" for r in seen)
        await operator.run("health")
        await operator.run("health")
    posts = [r for r in seen if r.method == "POST"]
    assert len(posts) == 1
    assert set(json.loads(posts[0].content)) == {"message_type", "content"}
    assert "HTTP 503" in json.loads(posts[0].content)["content"]
    assert all(r.url.path == "/health" for r in seen if r.method == "GET")
    assert (state / "health.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_health_network_unknown_not_claimed_offline(tmp_path):
    seen = []

    def respond(request):
        if request.method == "GET":
            raise httpx.ConnectError("SYNTHETIC PRIVATE ERROR", request=request)
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok", "message_id": RECEIPT})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        operator = OperatorController(
            tmp_path / "state",
            client,
            OperatorPublisher(client, "SYNTHETIC"),
            lambda: NOW,
        )
        await operator.run("health")
        await operator.run("health")
    assert "unavailable" in seen[0]["content"]
    assert "offline" not in seen[0]["content"]
    assert "PRIVATE" not in seen[0]["content"]


@pytest.mark.asyncio
async def test_real_lock_blocks_other_mode_before_network(tmp_path, transport):
    mock, seen, _statuses = transport
    async with httpx.AsyncClient(transport=mock) as client:
        operator = OperatorController(
            tmp_path / "state",
            client,
            OperatorPublisher(client, "SYNTHETIC"),
            lambda: NOW,
        )
        with controller_lock(tmp_path / "state") as acquired:
            assert acquired
            assert await operator.run("health") == "busy"
    assert not seen


@pytest.fixture
def daily_transport(tmp_path):
    seen = []

    def respond(request):
        seen.append(request)
        if request.method == "POST":
            with sqlite3.connect(tmp_path / "state" / "daily-ledger.sqlite") as db:
                assert (
                    db.execute(
                        "SELECT COUNT(*) FROM publications WHERE state='reserved'"
                    ).fetchone()[0]
                    >= 1
                )
            if request.url.path.endswith("/message"):
                return httpx.Response(200, json={"status": "ok", "message_id": RECEIPT})
            return httpx.Response(
                201,
                json={
                    "id": RECEIPT,
                    "agent_id": RECEIPT,
                    "title": "Daily",
                    "created_at": NOW.isoformat(),
                },
            )
        if request.url.path.endswith("/stats"):
            return httpx.Response(
                200,
                json=dict.fromkeys(
                    (
                        "total_agents",
                        "active_agents",
                        "total_projects",
                        "total_code_commits",
                        "total_reviews",
                        "projects_deployed",
                        "total_repos_created",
                        "total_prs_merged",
                    ),
                    0,
                ),
            )
        return httpx.Response(200, json=[])

    return httpx.MockTransport(respond), seen


@pytest.fixture
def analyst(monkeypatch):
    received = []

    async def answer(bundle):
        received.append(bundle)
        return {
            "analysis": json.dumps(
                {
                    "snapshot_digest": bundle["snapshot_digest"],
                    "highlights": [],
                    "attention": [],
                    "recommendations": [],
                }
            ),
            "usage": USAGE,
        }

    monkeypatch.setattr("agentspore_sdk.operator_controller.ask_analyst", answer)
    return received


@pytest.mark.asyncio
async def test_daily_reserve_before_posts_and_private_key_absent_from_analysis(
    tmp_path, daily_transport, analyst
):
    mock, seen = daily_transport
    async with httpx.AsyncClient(transport=mock) as client:
        operator = OperatorController(
            tmp_path / "state",
            client,
            OperatorPublisher(client, "SYNTHETIC"),
            lambda: NOW,
        )
        assert await operator.run("daily") == "daily_complete"
        assert await operator.run("daily") == "daily_reserved"
    assert len([r for r in seen if r.method == "POST"]) == 2
    assert "SYNTHETIC" not in json.dumps(analyst)
    assert all("x-api-key" not in r.headers for r in seen if r.method == "GET")


@pytest.mark.asyncio
async def test_outside_schedule_does_not_collect_or_call_model(
    tmp_path, daily_transport, analyst
):
    mock, seen = daily_transport
    async with httpx.AsyncClient(transport=mock) as client:
        operator = OperatorController(
            tmp_path / "state",
            client,
            OperatorPublisher(client, "SYNTHETIC"),
            lambda: NOW.replace(hour=16),
        )
        assert await operator.run("daily") == "outside_window"
    assert not seen and not analyst


@pytest.mark.asyncio
async def test_analysis_quota_blocks_fifth_ipc_and_survives_restart(
    tmp_path, daily_transport, analyst
):
    mock, _seen = daily_transport
    async with httpx.AsyncClient(transport=mock) as client:
        for _ in range(4):
            operator = OperatorController(
                tmp_path / "state",
                client,
                OperatorPublisher(client, "SYNTHETIC"),
                lambda: NOW,
            )
            await operator.run("daily")
        assert await operator.run("daily") == "analysis_quota"
    assert len(analyst) == 4


@pytest.mark.asyncio
async def test_forged_model_operation_cannot_publish(
    tmp_path, daily_transport, monkeypatch
):
    async def forged(bundle):
        return {
            "analysis": json.dumps(
                {
                    "snapshot_digest": bundle["snapshot_digest"],
                    "url": "http://127.0.0.1/delete",
                }
            ),
            "usage": USAGE,
        }

    monkeypatch.setattr("agentspore_sdk.operator_controller.ask_analyst", forged)
    mock, seen = daily_transport
    async with httpx.AsyncClient(transport=mock) as client:
        operator = OperatorController(
            tmp_path / "state",
            client,
            OperatorPublisher(client, "SYNTHETIC"),
            lambda: NOW,
        )
        assert await operator.run("daily") == "analysis_unavailable"
    assert not any(r.method == "POST" for r in seen)
    with sqlite3.connect(tmp_path / "state" / "daily-ledger.sqlite") as db:
        assert db.execute("SELECT state FROM analyses").fetchone()[0] == "failed"


@pytest.mark.asyncio
async def test_ambiguous_delivery_is_not_retried(tmp_path, daily_transport, analyst):
    transport, seen = daily_transport

    async def respond(request):
        if request.method == "POST":
            seen.append(request)
            raise httpx.ReadTimeout("SYNTHETIC", request=request)
        return await transport.handle_async_request(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        operator = OperatorController(
            tmp_path / "state",
            client,
            OperatorPublisher(client, "SYNTHETIC"),
            lambda: NOW,
        )
        await operator.run("daily")
        await operator.run("daily")
    assert len([r for r in seen if r.method == "POST"]) == 2
    with sqlite3.connect(tmp_path / "state" / "daily-ledger.sqlite") as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM publications WHERE state='uncertain'"
            ).fetchone()[0]
            == 2
        )


@pytest.mark.asyncio
async def test_crash_leaves_reservation_that_prevents_repost(
    tmp_path, daily_transport, analyst
):
    transport, seen = daily_transport

    async def respond(request):
        if request.method == "POST":
            seen.append(request)
            raise RuntimeError("synthetic process crash")
        return await transport.handle_async_request(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        operator = OperatorController(
            tmp_path / "state",
            client,
            OperatorPublisher(client, "SYNTHETIC"),
            lambda: NOW,
        )
        with pytest.raises(RuntimeError):
            await operator.run("daily")
    async with httpx.AsyncClient(transport=transport) as client:
        await OperatorController(
            tmp_path / "state",
            client,
            OperatorPublisher(client, "SYNTHETIC"),
            lambda: NOW,
        ).run("daily")
    assert len([r for r in seen if r.url.path.endswith("/message")]) == 1


@pytest.mark.asyncio
async def test_publication_finish_uses_original_moscow_day(tmp_path):
    current = [datetime(2026, 10, 21, 20, 59, 59, tzinfo=UTC)]

    def respond(request):
        if request.method == "GET":
            return httpx.Response(503)
        current[0] = datetime(2026, 10, 21, 21, 0, 1, tzinfo=UTC)
        return httpx.Response(200, json={"status": "ok", "message_id": RECEIPT})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        operator = OperatorController(
            tmp_path / "state",
            client,
            OperatorPublisher(client, "SYNTHETIC"),
            lambda: current[0],
        )
        await operator.run("health")
        assert await operator.run("health") == "sent"
    with sqlite3.connect(tmp_path / "state" / "incident-ledger.sqlite") as db:
        assert db.execute("SELECT local_day,state FROM publications").fetchone() == (
            "2026-10-21",
            "sent",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("oversized", [False, True])
async def test_native_socket_framing_has_fixed_path_and_response_bound(
    monkeypatch, oversized
):

    left, right = socket.socketpair()
    left.setblocking(False)
    right.setblocking(False)

    async def connect(path):
        assert path == "/run/agentspore-operator/analyst.sock"
        return await asyncio.open_connection(sock=left)

    async def native():
        reader, writer = await asyncio.open_connection(sock=right)
        length = struct.unpack("!I", await reader.readexactly(4))[0]
        assert json.loads(await reader.readexactly(length)) == {"public": "synthetic"}
        body = json.dumps({"analysis": "{}", "usage": USAGE}).encode()
        writer.write(struct.pack("!I", 65537 if oversized else len(body)))
        if not oversized:
            writer.write(body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    monkeypatch.setattr(module.asyncio, "open_unix_connection", connect)
    monkeypatch.setattr(module, "check_analyst_peer", lambda _writer: None)
    task = asyncio.create_task(native())
    try:
        if oversized:
            with pytest.raises(ValueError):
                await module.ask_analyst({"public": "synthetic"})
        else:
            assert await module.ask_analyst({"public": "synthetic"}) == {
                "analysis": "{}",
                "usage": USAGE,
            }
        await task
    finally:
        left.close()
        right.close()


def test_cli_rejects_arbitrary_operation_before_credentials(monkeypatch):
    monkeypatch.setattr(module.sys, "argv", ["operator", "delete-users"])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2


@pytest.mark.asyncio
async def test_native_timeout_retains_active_slot_until_trusted_recovery(
    tmp_path, daily_transport, monkeypatch
):
    async def timeout(_bundle):
        raise TimeoutError("synthetic unknown native outcome")

    monkeypatch.setattr(module, "ask_analyst", timeout)
    mock, seen = daily_transport
    async with httpx.AsyncClient(transport=mock) as client:
        operator = OperatorController(
            tmp_path / "state",
            client,
            OperatorPublisher(client, "SYNTHETIC"),
            lambda: NOW,
        )
        assert await operator.run("daily") == "analysis_uncertain"
        assert await operator.run("daily") == "analysis_quota"
    assert not any(r.method == "POST" for r in seen)
    with sqlite3.connect(tmp_path / "state" / "daily-ledger.sqlite") as db:
        assert db.execute("SELECT state FROM analyses").fetchone()[0] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["pid", "uid", "parent_owner", "socket_mode"])
async def test_untrusted_socket_activation_peer_or_path_is_rejected(monkeypatch, fault):
    directory = SimpleNamespace(
        st_mode=module.stat.S_IFDIR | 0o750, st_uid=0, st_gid=1234
    )
    endpoint = SimpleNamespace(
        st_mode=module.stat.S_IFSOCK | 0o600, st_uid=module.os.getuid()
    )
    peer = [1, 0, 0]
    if fault == "pid":
        peer[0] = 7
    elif fault == "uid":
        peer[1] = 7
    elif fault == "parent_owner":
        directory.st_uid = 7
    else:
        endpoint.st_mode = module.stat.S_IFSOCK | 0o666
    path = SimpleNamespace(
        parent=SimpleNamespace(lstat=lambda: directory), lstat=lambda: endpoint
    )
    left, right = socket.socketpair()
    left.setblocking(False)
    _reader, writer = await asyncio.open_connection(sock=left)
    monkeypatch.setattr(
        writer,
        "get_extra_info",
        lambda _name: SimpleNamespace(
            getsockopt=lambda *_args: struct.pack("3i", *peer)
        ),
    )
    monkeypatch.setattr(module, "Path", lambda _path: path)
    monkeypatch.setattr(
        module,
        "pwd",
        SimpleNamespace(getpwnam=lambda _name: SimpleNamespace(pw_gid=1234)),
    )
    monkeypatch.setattr(module, "PEER_OPTION", 17)
    try:
        with pytest.raises(PermissionError):
            module.check_analyst_peer(writer)
    finally:
        writer.close()
        await writer.wait_closed()
        right.close()


@pytest.mark.asyncio
async def test_unsafe_state_permissions_rejected_before_network(tmp_path, transport):
    state = tmp_path / "state"
    state.mkdir()
    state.chmod(0o755)
    mock, seen, _statuses = transport
    async with httpx.AsyncClient(transport=mock) as client:
        operator = OperatorController(
            state, client, OperatorPublisher(client, "SYNTHETIC"), lambda: NOW
        )
        with pytest.raises(PermissionError):
            await operator.run("health")
    assert not seen


@pytest.mark.parametrize(
    "usage",
    [
        {"output_tokens": True},
        {"input_tokens": -1, "cached_input_tokens": 0, "output_tokens": 3},
        {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 3, "command": 1},
    ],
)
def test_invalid_native_usage_cannot_be_accepted(usage):
    with pytest.raises(ValueError):
        module.checked_reply({"analysis": "{}", "usage": usage})


def test_module_entrypoint_has_reply_validator_before_startup(monkeypatch, capsys):
    def invoke_without_external_actions(coroutine):
        namespace = coroutine.cr_frame.f_globals
        coroutine.close()
        assert (
            namespace["checked_reply"]({"analysis": "{}", "usage": USAGE})["usage"]
            == USAGE
        )
        return "synthetic_entry_verified"

    monkeypatch.setattr(module.sys, "argv", ["operator", "daily"])
    monkeypatch.setattr(module.asyncio, "run", invoke_without_external_actions)
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        runpy.run_module("agentspore_sdk.operator_controller", run_name="__main__")
    assert json.loads(capsys.readouterr().out) == {"result": "synthetic_entry_verified"}

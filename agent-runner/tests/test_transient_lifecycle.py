"""Isolated runner HTTP lifecycle; only sandbox, provider and clock boundaries are replaced."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import main
import pytest
import routes.agents as agents_mod
import session as session_mod
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_backends.types import ExecuteResponse, WriteResult


@pytest.fixture
def sandbox_adapter(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace only the outward sandbox backend; lifecycle remains real."""
    sandbox = MagicMock(spec=agents_mod.SecureDockerSandbox)
    sandbox.read.return_value = ""
    sandbox.read_bytes.return_value = b""
    sandbox.write.return_value = WriteResult(path="/workspace/result.txt")
    sandbox.execute.return_value = ExecuteResponse(output="", exit_code=0)
    sandbox.is_alive.return_value = True
    monkeypatch.setattr(
        agents_mod, "SecureDockerSandbox", MagicMock(return_value=sandbox)
    )
    return sandbox


@pytest.fixture
def model_adapter() -> SimpleNamespace:
    """Replace paid model inference while retaining actual deep-agent orchestration."""
    tools_seen = []
    mode = {"value": "success"}
    streaming = asyncio.Event()

    def provider(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tools_seen.extend(tool.name for tool in info.function_tools)
        if mode["value"] == "error":
            raise RuntimeError("provider rejected test request")
        return ModelResponse(parts=[TextPart(content="Session answer")])

    async def provider_stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        tools_seen.extend(tool.name for tool in info.function_tools)
        if mode["value"] == "error":
            raise RuntimeError("provider rejected test request")
        yield "Session answer"
        if mode["value"] == "blocked":
            streaming.set()
            await asyncio.Event().wait()

    model = FunctionModel(function=provider, stream_function=provider_stream)
    return SimpleNamespace(
        model=model, tools=tools_seen, mode=mode, streaming=streaming
    )


@pytest.fixture
async def transient_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sandbox_adapter: MagicMock,
    model_adapter: SimpleNamespace,
) -> AsyncIterator[SimpleNamespace]:
    """Use real app, session, deep agent, locks and cleanup with outward adapters only."""
    outbound = []
    docker_client = MagicMock()
    docker_client.containers.list.return_value = []
    monkeypatch.setattr(main.docker, "from_env", lambda: docker_client)
    monkeypatch.setattr(main.settings, "workspace_root", tmp_path)
    monkeypatch.setattr(main.settings, "llm_proxy_url", "")
    monkeypatch.setattr(main.settings, "llm_proxy_chain", "")

    def platform(request: httpx.Request) -> httpx.Response:
        outbound.append(request.url.path)
        return httpx.Response(200, text="# Test platform skill\n")

    monkeypatch.setattr(
        agents_mod,
        "OpenAIChatModel",
        lambda *args, **kwargs: model_adapter.model,
    )
    transport = httpx.MockTransport(platform)
    original_transport = httpx.AsyncClient._transport_for_url

    def outbound_transport(
        client: httpx.AsyncClient, url: httpx.URL
    ) -> httpx.AsyncBaseTransport:
        if url.host == "runner.test":
            return original_transport(client, url)
        return transport

    monkeypatch.setattr(httpx.AsyncClient, "_transport_for_url", outbound_transport)
    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://runner.test",
            headers={"X-Runner-Key": main.settings.runner_key},
        ) as client:
            yield SimpleNamespace(
                client=client,
                sandbox=sandbox_adapter,
                outbound=outbound,
                tools=model_adapter.tools,
                mode=model_adapter.mode,
                streaming=model_adapter.streaming,
                docker=docker_client,
            )


@pytest.fixture
async def started_session(
    transient_runtime: SimpleNamespace,
) -> AsyncIterator[SimpleNamespace]:
    """Start a transient session with its platform heartbeat disabled."""
    runtime = transient_runtime
    session_id = "session-00000000-0000-0000-0000-000000000035-left-12345678"
    response = await runtime.client.post(
        f"/agents/{session_id}/start",
        json={
            "agent_id": session_id,
            "system_prompt": "Answer the session question.",
            "model": "test/session",
            "provider_base_url": "https://provider.invalid/v1",
            "provider_api_key": "synthetic-provider-key",
            "heartbeat_seconds": 0,
        },
    )
    assert response.status_code == 200
    runtime.session_id = session_id
    runtime.session = main.sessions[session_id]
    yield runtime
    await runtime.client.post(f"/agents/{session_id}/stop")


async def test_startup_keeps_reaper_without_restoring_hosted(
    transient_runtime: SimpleNamespace,
) -> None:
    """Startup performs sandbox discovery without a hosted lifecycle HTTP request."""
    assert transient_runtime.outbound == []
    transient_runtime.docker.containers.list.assert_called_once()
    assert not main.sessions


async def test_session_http_start_chat_stop_preserves_workspace(
    started_session, tmp_path
):
    """The actual transient API runs a model turn and releases owned resources."""
    runtime = started_session
    session = runtime.session
    eviction = session.worker_pool._eviction_task
    assert session.heartbeat_seconds == 0
    assert session.heartbeat_task is None
    assert session.ws_task is None
    assert eviction is not None
    response = await runtime.client.post(
        f"/agents/{runtime.session_id}/chat",
        json={"content": "Give the result."},
    )
    assert response.status_code == 200
    assert response.json()["reply"] == "Session answer"
    assert session.message_history
    assert "search_past_runs" not in runtime.tools
    stopped = await runtime.client.post(f"/agents/{runtime.session_id}/stop")
    assert stopped.status_code == 200
    assert runtime.session_id not in main.sessions
    assert session.worker_pool._eviction_task is None
    done, pending = await asyncio.wait({eviction}, timeout=1)
    assert done == {eviction}
    assert not pending
    assert eviction.cancelling() == 1
    assert eviction.cancelled() or eviction.exception() is None
    runtime.sandbox.stop.assert_called_once()
    assert (tmp_path / runtime.session_id / "AGENT.md").is_file()
    assert runtime.outbound == ["/skill.md"]


@pytest.mark.parametrize("stream", [False, True])
async def test_failed_session_turn_releases_lock_and_can_stop(
    started_session: SimpleNamespace,
    stream: bool,
) -> None:
    """Provider failure leaves lifecycle cleanup and later requests usable."""
    runtime = started_session
    runtime.mode["value"] = "error"
    path = f"/agents/{runtime.session_id}/chat" + ("/stream" if stream else "")
    response = await runtime.client.post(path, json={"content": "Fail this turn."})
    if stream:
        assert any(
            json.loads(line)["type"] == "error" for line in response.text.splitlines()
        )
    else:
        assert response.status_code == 500
    assert not runtime.session.chat_lock.locked()
    assert runtime.session.active_session_id is None
    runtime.mode["value"] = "success"
    next_turn = await runtime.client.post(path, json={"content": "Try again."})
    assert next_turn.status_code == 200
    assert "Session answer" in next_turn.text
    stopped = await runtime.client.post(f"/agents/{runtime.session_id}/stop")
    assert stopped.status_code == 200
    runtime.sandbox.stop.assert_called_once()
    assert runtime.outbound == ["/skill.md"]


async def test_cancelled_session_stream_releases_lock_before_stop(
    started_session: SimpleNamespace,
) -> None:
    """Cancellation reaches the real HTTP stream and its generator's finally."""
    runtime = started_session
    runtime.mode["value"] = "blocked"
    request = asyncio.create_task(
        runtime.client.post(
            f"/agents/{runtime.session_id}/chat/stream",
            json={"content": "Stream the result."},
        )
    )
    try:
        await asyncio.wait_for(runtime.streaming.wait(), timeout=5)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    finally:
        if not request.done():
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
    assert not runtime.session.chat_lock.locked()
    assert runtime.session.active_session_id is None
    stopped = await runtime.client.post(f"/agents/{runtime.session_id}/stop")
    assert stopped.status_code == 200
    runtime.sandbox.stop.assert_called_once()
    assert runtime.outbound == ["/skill.md"]


async def test_idle_cleanup_stops_session_without_hosted_callback(
    started_session, monkeypatch
):
    """Expiry retains workspace data and stops the sandbox without hosted writes."""
    runtime = started_session
    runtime.session.last_activity = 0
    ticks = iter([None])

    async def clock_tick(delay: float) -> None:
        if next(ticks, "cancel") == "cancel":
            raise asyncio.CancelledError

    monkeypatch.setattr(session_mod.asyncio, "sleep", clock_tick)
    with pytest.raises(asyncio.CancelledError):
        await session_mod.idle_cleanup_loop()
    assert runtime.session_id not in main.sessions
    runtime.sandbox.stop.assert_called_once()
    assert runtime.session.worker_pool._eviction_task is None
    assert runtime.outbound == ["/skill.md"]


async def test_real_runner_auth_blocks_transient_start(
    transient_runtime: SimpleNamespace,
) -> None:
    """Removing hosted management never removes runner authentication."""
    response = await transient_runtime.client.post(
        "/agents/session-denied/start",
        headers={"X-Runner-Key": ""},
        json={
            "agent_id": "session-denied",
            "system_prompt": "Denied",
            "heartbeat_seconds": 0,
        },
    )
    assert response.status_code == 403
    assert "session-denied" not in main.sessions
    assert transient_runtime.outbound == []

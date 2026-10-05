"""Exercise the REST contract and constrained demo entrypoint without production."""

import json

import httpx
import pytest
import pytest_asyncio
from agentspore_sdk import AgentClient

from examples import first_result

TASK_ID = "12345678-1234-1234-1234-123456789012"
KEY = "af_synthetic_do_not_print"


@pytest_asyncio.fixture
async def client():
    instance = AgentClient(KEY, "https://example.test")
    await instance._http.aclose()
    yield instance
    await instance.stop()


@pytest.mark.parametrize("method", ["claim_task", "complete_task"])
@pytest.mark.parametrize("status", [200, 401, 403, 404, 409, 500])
async def test_rest_contract(client, method, status):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"status": "server-result"})

    client._http = httpx.AsyncClient(
        base_url=client.base_url,
        headers={"X-API-Key": KEY},
        transport=httpx.MockTransport(respond),
    )
    args = (TASK_ID, "artifact proof") if method == "complete_task" else (TASK_ID,)
    if status == 200:
        assert await getattr(client, method)(*args) == {"status": "server-result"}
    else:
        with pytest.raises(httpx.HTTPStatusError):
            await getattr(client, method)(*args)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.headers["X-API-Key"] == KEY
    suffix = "complete" if method == "complete_task" else "claim"
    assert request.url == f"https://example.test/api/v1/agents/tasks/{TASK_ID}/{suffix}"
    if method == "complete_task":
        assert json.loads(request.content) == {"result": "artifact proof"}


@pytest.mark.parametrize("method", ["claim_task", "complete_task"])
async def test_network_failure_not_retried(client, method):
    requests = []

    def disconnect(request):
        requests.append(request)
        raise httpx.ReadTimeout("Synthetic timeout", request=request)

    client._http = httpx.AsyncClient(
        base_url=client.base_url, transport=httpx.MockTransport(disconnect)
    )
    with pytest.raises(httpx.ReadTimeout):
        args = (TASK_ID, "proof") if method == "complete_task" else (TASK_ID,)
        await getattr(client, method)(*args)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "scenario",
    ["success", "invalid", "ordinary", "missing", "timeout", "filename", "description"],
)
async def test_demo_entrypoint(client, tmp_path, monkeypatch, capsys, scenario):
    requests = []
    artifact = tmp_path / "onboarding.txt"
    task = {
        "id": TASK_ID,
        "title": first_result.DEMO_TITLE,
        "description": first_result.DEMO_DESCRIPTION,
    }
    if scenario == "filename":
        artifact = tmp_path / "wrong.txt"
    if scenario == "description":
        task["description"] = "Another criterion"
    if scenario == "ordinary":
        task["title"] = "Fix a production bug"

    def respond(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[] if scenario == "missing" else [task])
        if request.url.path.endswith("/complete"):
            assert artifact.read_bytes() == first_result.ARTIFACT_CONTENT
            assert KEY not in request.content.decode()
            if scenario == "timeout":
                raise httpx.ReadTimeout("Synthetic timeout", request=request)
        return httpx.Response(200, json={"status": "completed"})

    def corrupt(path):
        path.write_bytes(b"corrupt")

    client._http = httpx.AsyncClient(
        base_url=client.base_url, transport=httpx.MockTransport(respond)
    )
    monkeypatch.setenv("AGENTSPORE_API_KEY", KEY)
    monkeypatch.setattr(first_result, "AgentClient", lambda *args, **kwargs: client)
    if scenario == "invalid":
        monkeypatch.setattr(first_result, "write_artifact", corrupt)
    argv = ["--task-id", TASK_ID, "--artifact", str(artifact)]
    assert await first_result.main(argv) == (0 if scenario == "success" else 1)
    completed = [r for r in requests if r.url.path.endswith("/complete")]
    assert len(completed) == (1 if scenario in ("success", "timeout") else 0)
    if scenario == "success":
        count = len(requests)
        assert await first_result.main(argv) == 1
        assert len(requests) == count
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err
    if artifact.exists():
        assert KEY not in artifact.read_text()


@pytest.mark.parametrize("argv,key", [([], KEY), (["--task-id", TASK_ID], "")])
async def test_missing_inputs_no_client(argv, key, monkeypatch, capsys):
    monkeypatch.setenv("AGENTSPORE_API_KEY", key)
    assert await first_result.main(argv) == 1
    assert KEY not in capsys.readouterr().err

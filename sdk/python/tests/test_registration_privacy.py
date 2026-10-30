"""Registration preserves its contract without exposing synthetic response bodies."""

import json

import httpx
import pytest
from agentspore import AgentSpore
from agentspore.exceptions import APIError

BASE_URL = "https://registration.invalid/prefix/"
MARKER = "SYNTHETIC_PRIVATE_RESPONSE_MARKER"
SAFE_DETAIL = "Registration failed"


@pytest.fixture
def install_transport(monkeypatch):
    real_client = httpx.Client
    requests = []

    def install(respond):
        def handle(request):
            requests.append(request)
            assert request.extensions["timeout"] == dict.fromkeys(
                ["connect", "read", "write", "pool"], 30.0
            )
            return respond(request)

        class TransportClient(real_client):
            def __init__(self, **kwargs):
                super().__init__(**kwargs, transport=httpx.MockTransport(handle))

        monkeypatch.setattr(httpx, "Client", TransportClient)
        return requests

    return install


def test_registration_returns_authenticated_client(install_transport):
    def respond(request):
        assert request.method == "POST"
        if request.url.path.endswith("/register"):
            assert request.url == BASE_URL + "api/v1/agents/register"
            assert "X-API-Key" not in request.headers
            assert json.loads(request.content) == {
                "name": "SyntheticAgent",
                "specialization": "programmer",
                "skills": ["python"],
                "model_provider": "synthetic-provider",
                "model_name": "synthetic-model",
                "description": "Synthetic description",
            }
            return httpx.Response(201, json={"api_key": "synthetic-test-key"})
        assert request.url == BASE_URL + "api/v1/agents/heartbeat"
        assert request.headers["X-API-Key"] == "synthetic-test-key"
        assert json.loads(request.content)["status"] == "idle"
        return httpx.Response(200, json={"tasks": [], "notifications": ["synthetic"]})

    requests = install_transport(respond)
    with AgentSpore.register(
        "SyntheticAgent",
        "programmer",
        skills=["python"],
        model_provider="synthetic-provider",
        model_name="synthetic-model",
        description="Synthetic description",
        base_url=BASE_URL,
    ) as client:
        assert isinstance(client, AgentSpore)
        assert client.base_url == BASE_URL.rstrip("/")
        assert client.heartbeat().notifications == ["synthetic"]
    assert len(requests) == 2


def test_registration_defaults_remain_ownerless(install_transport):
    def respond(request):
        assert request.url == "https://agentspore.com/api/v1/agents/register"
        assert json.loads(request.content) == {
            "name": "SyntheticAgent",
            "specialization": "programmer",
            "skills": [],
            "model_provider": "openai",
            "model_name": "gpt-4o",
            "description": "",
        }
        return httpx.Response(200, json={"api_key": "synthetic-test-key"})

    requests = install_transport(respond)
    with AgentSpore.register("SyntheticAgent", "programmer"):
        pass
    assert len(requests) == 1


@pytest.mark.parametrize("arguments", [{}, {"name": "SyntheticAgent"}])
def test_missing_required_arguments_send_no_request(install_transport, arguments):
    def respond(_request):
        pytest.fail("Missing Python arguments must not reach the transport")

    requests = install_transport(respond)
    with pytest.raises(TypeError):
        AgentSpore.register(**arguments)
    assert requests == []


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (422, {"detail": [{"loc": ["body", "name"], "input": MARKER}]}),
        (400, {"detail": MARKER}),
        (401, {"detail": MARKER}),
        (404, {"detail": MARKER}),
        (500, MARKER),
        (502, "<html>" + MARKER + "</html>"),
        (503, ""),
    ],
)
def test_registration_error_uses_safe_detail(install_transport, status, body):
    def respond(_request):
        if isinstance(body, dict):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=body)

    requests = install_transport(respond)
    with pytest.raises(APIError) as caught:
        AgentSpore.register("SyntheticAgent", "programmer", base_url=BASE_URL)
    error = caught.value
    assert type(error) is APIError
    assert error.status_code == status
    assert error.detail == SAFE_DETAIL
    assert str(error) == f"API error {status}: {SAFE_DETAIL}"
    assert MARKER not in repr(error)
    assert len(requests) == 1


def test_registration_timeout_is_not_retried(install_transport):
    timeout = httpx.ReadTimeout("Synthetic transport timeout")

    def respond(_request):
        raise timeout

    requests = install_transport(respond)
    with pytest.raises(httpx.ReadTimeout) as caught:
        AgentSpore.register("SyntheticAgent", "programmer", base_url=BASE_URL)
    assert caught.value is timeout
    assert len(requests) == 1

"""Unit tests: probe URL filter and probe HTTP behaviour (only outbound HTTP is mocked)."""

from __future__ import annotations

import httpx
import pytest

from app.services.service_health import PROBE_USER_AGENT, is_probeable, probe


@pytest.mark.parametrize(
    "url",
    [
        "https://agentspore.com/health",
        "https://agentspore.ru/",
        "https://pereklichka.agentspore.com/privacy",
        "https://a.b.agentspore.ru/x?y=1",
        "https://AgentSpore.COM/up",
    ],
)
def test_allowed_hosts_are_probeable(url: str) -> None:
    assert is_probeable(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://agentspore.com/health",
        "https://example.com/",
        "https://agentspore.com.evil.io/",
        "https://evilagentspore.com/",
        "https://evil.io/agentspore.com",
        "https://127.0.0.1/",
        "https://[::1]/",
        "https://169.254.169.254/latest",
        "https://user:pw@agentspore.com/",
        "https://agentspore.com@evil.io/",
        "https://agentspore.com\\@evil.io/",
        "https://agentspore.com:8443/",
        "https://agentspore.com:443/",
        "https://agentspore.com./",
        "https://.agentspore.com/",
        "ftp://agentspore.com/",
        "https:///agentspore.com",
        "t.me/bot",
        "",
    ],
)
def test_foreign_or_unsafe_urls_are_rejected(url: str) -> None:
    assert not is_probeable(url)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


async def test_2xx_returns_status_and_sends_user_agent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    async with _client(handler) as client:
        assert await probe(client, "https://agentspore.com/h") == 200
    assert seen[0].method == "GET"
    assert seen[0].headers["user-agent"] == PROBE_USER_AGENT == "AgentSporeHealthProbe/1"


async def test_5xx_returns_status() -> None:
    async with _client(lambda r: httpx.Response(503)) as client:
        assert await probe(client, "https://agentspore.com/h") == 503


async def test_redirect_is_not_followed() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.io/"})

    # Client is built with follow_redirects=True: probe itself must override it.
    async with _client(handler) as client:
        assert await probe(client, "https://agentspore.com/h") == 302
    assert calls == ["https://agentspore.com/h"]


async def test_timeout_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("t", request=request)

    async with _client(handler) as client:
        assert await probe(client, "https://agentspore.com/h") is None


async def test_connect_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("c", request=request)

    async with _client(handler) as client:
        assert await probe(client, "https://agentspore.com/h") is None


async def test_unprobeable_url_makes_no_request() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200)

    async with _client(handler) as client:
        assert await probe(client, "https://evil.io/") is None
    assert calls == []


async def test_body_read_is_capped_at_64kb() -> None:
    class Endless(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.sent = 0

        async def __aiter__(self):
            while True:
                self.sent += 1024
                yield b"x" * 1024

    stream = Endless()
    async with _client(lambda r: httpx.Response(200, stream=stream)) as client:
        assert await probe(client, "https://agentspore.com/h") == 200
    assert stream.sent <= 64 * 1024 + 1024

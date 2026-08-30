"""Tests for the reactive proxy fallback chain.

Scope: unit, driven through the real ProxyChainTransport / AsyncClient — the
fake transports below simulate connect failures, they do not mock the
chain-advance logic itself.
"""

from __future__ import annotations

import httpx
import pytest

import routes.agents as agents_mod
from proxy_chain import ProxyChainTransport, _mask


class FakeTransport(httpx.AsyncBaseTransport):
    """Stands in for httpx.AsyncHTTPTransport: either raises or returns 200."""

    def __init__(self, fail: bool = False, exc: Exception | None = None) -> None:
        self.fail = fail or exc is not None
        self.exc = exc
        self.calls = 0
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.fail:
            raise self.exc or httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    async def aclose(self) -> None:
        self.closed = True


def _install(chain: ProxyChainTransport, transports: list[FakeTransport]) -> None:
    chain._transports = transports  # noqa: SLF001 — test wiring around real proxy construction


class TestConfigDefault:
    def test_chain_unset_falls_back_to_single_proxy_url(self, monkeypatch):
        monkeypatch.setattr(agents_mod.settings, "llm_proxy_chain", "")
        monkeypatch.setattr(agents_mod.settings, "llm_proxy_url", "http://127.0.0.1:3128")

        client = agents_mod.build_llm_http_client()

        assert client is not None
        assert client._transport is not None
        assert not isinstance(client._transport, ProxyChainTransport)

    def test_chain_and_url_both_unset_returns_none(self, monkeypatch):
        monkeypatch.setattr(agents_mod.settings, "llm_proxy_chain", "")
        monkeypatch.setattr(agents_mod.settings, "llm_proxy_url", "")

        assert agents_mod.build_llm_http_client() is None

    def test_chain_set_builds_chain_transport(self, monkeypatch):
        monkeypatch.setattr(
            agents_mod.settings, "llm_proxy_chain", "http://a:3128,http://b:3128"
        )

        client = agents_mod.build_llm_http_client()

        assert isinstance(client._transport, ProxyChainTransport)
        assert len(client._transport._transports) == 2


class TestChainAdvance:
    @pytest.mark.asyncio
    async def test_first_proxy_dead_second_succeeds(self):
        chain = ProxyChainTransport(["http://a:1", "http://b:1"])
        dead, alive = FakeTransport(fail=True), FakeTransport(fail=False)
        _install(chain, [dead, alive])

        request = httpx.Request("POST", "http://example.com/chat/completions")
        response = await chain.handle_async_request(request)

        assert response.status_code == 200
        assert dead.calls == 1
        assert alive.calls == 1
        assert chain._current == 1

    @pytest.mark.asyncio
    async def test_working_proxy_remembered_across_requests(self):
        chain = ProxyChainTransport(["http://a:1", "http://b:1"])
        dead, alive = FakeTransport(fail=True), FakeTransport(fail=False)
        _install(chain, [dead, alive])

        request = httpx.Request("POST", "http://example.com/chat/completions")
        await chain.handle_async_request(request)
        await chain.handle_async_request(request)

        # Second request must not re-try the dead proxy first.
        assert dead.calls == 1
        assert alive.calls == 2

    @pytest.mark.asyncio
    async def test_all_proxies_failing_raises(self):
        chain = ProxyChainTransport(["http://a:1", "http://b:1"])
        _install(chain, [FakeTransport(fail=True), FakeTransport(fail=True)])

        request = httpx.Request("POST", "http://example.com/chat/completions")
        with pytest.raises(httpx.ConnectError):
            await chain.handle_async_request(request)

    @pytest.mark.asyncio
    async def test_http_error_response_does_not_advance_chain(self):
        """An HTTP 500 from the model is not a transport failure — no switch."""
        chain = ProxyChainTransport(["http://a:1", "http://b:1"])

        class ErrorTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(500, request=request)

        first, second = ErrorTransport(), FakeTransport(fail=False)
        _install(chain, [first, second])

        request = httpx.Request("POST", "http://example.com/chat/completions")
        response = await chain.handle_async_request(request)

        assert response.status_code == 500
        assert chain._current == 0
        assert second.calls == 0


class TestMutation:
    """Break advance-on-failure -> RED. Restore -> GREEN. See dev report."""

    @pytest.mark.asyncio
    async def test_advance_logic_is_load_bearing(self):
        chain = ProxyChainTransport(["http://a:1", "http://b:1"])
        dead, alive = FakeTransport(fail=True), FakeTransport(fail=False)
        _install(chain, [dead, alive])

        request = httpx.Request("POST", "http://example.com/chat/completions")
        response = await chain.handle_async_request(request)

        # If the offset loop is deleted (single-proxy try, no advance), this
        # raises ConnectError instead of returning 200 — the assertion below
        # is the one that goes red under that mutation.
        assert response.status_code == 200


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("refused"),
        httpx.ReadError("dropped"),
        httpx.ProxyError("proxy refused"),
        httpx.ConnectTimeout("timed out"),
        httpx.RemoteProtocolError("bad framing"),
    ],
)
@pytest.mark.asyncio
async def test_every_transport_failure_shape_advances(exc):
    """ReadError and ProxyError are siblings of ConnectError, not subclasses.

    A proxy that accepts the connection and then drops the response raises
    ReadError — one of the shapes measured in production, and one an explicit
    (ConnectError, TimeoutException, RemoteProtocolError) tuple silently
    misses.
    """
    chain = ProxyChainTransport(["http://dead:1", "http://alive:2"])
    _install(chain, [FakeTransport(exc=exc), FakeTransport()])

    response = await chain.handle_async_request(httpx.Request("GET", "https://example.com"))

    assert response.status_code == 200
    assert chain._current == 1  # noqa: SLF001


@pytest.mark.asyncio
async def test_aclose_closes_every_wrapped_transport():
    """The client is closed on three teardown paths; each must free all sockets."""
    chain = ProxyChainTransport(["http://a:1", "http://b:2"])
    transports = [FakeTransport(), FakeTransport()]
    _install(chain, transports)

    await chain.aclose()

    assert [t.closed for t in transports] == [True, True]


def test_mask_strips_credentials():
    """Today's proxies carry no credentials; other deployments' may."""
    masked = _mask("http://user:secretpw@1.2.3.4:3128")

    assert "secretpw" not in masked
    assert "user" not in masked
    assert masked == "http://1.2.3.4:3128"


def test_mask_omits_absent_port():
    assert _mask("http://1.2.3.4") == "http://1.2.3.4"

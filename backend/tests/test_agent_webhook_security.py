"""Webhook boundaries reject internal targets without touching external networks."""

import asyncio
import socket
import ssl
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.v1.agents import router, update_webhook
from app.core.database import get_db
from app.schemas.agents import AgentWebhookRequest
from app.services import webhook_destination as destination
from app.services.agent_service import get_agent_by_api_key
from app.services.agent_webhook_registration import AgentWebhookRegistration
from app.services.agent_webhook_service import AgentWebhookService


@pytest.fixture
async def dns(monkeypatch):
    lookup = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    return lookup


@pytest.fixture
def service():
    instance = object.__new__(AgentWebhookRegistration)
    instance.repo = AsyncMock()
    return instance


@pytest.mark.asyncio
async def test_registration_rejects_localhost_before_write():
    db = AsyncMock()
    with pytest.raises(HTTPException):
        await update_webhook(
            AgentWebhookRequest(url="http://localhost:8000/internal"), {"id": "agent"}, db
        )
    db.execute.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://localhost",
        "http://localhost.example",
        "https://",
        "https://user:pass@example.com",
        "https://example.com/#part",
        "https://example.com/\n",
        "https://example.com/ a",
        "https://example.com\\@127.0.0.1",
        "https://[fe80::1%25lo0]/",
        "https://example.com:99999/",
    ],
)
def test_invalid_urls(value):
    with pytest.raises(destination.UnsafeWebhookError):
        destination.parse_webhook_url(value)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "100.64.0.1",
        "192.0.0.9",
        "192.88.99.1",
        "224.0.0.1",
        "255.255.255.255",
        "192.0.2.1",
        "::",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "::ffff:8.8.8.8",
        "64:ff9b::808:808",
        "64:ff9b:1::1",
        "2001:1::1",
        "2002:808:808::",
        "2001:db8::1",
    ],
)
def test_special_networks_denied(address):
    assert not destination.public_address(address)


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", [[], ["8.8.8.8", "127.0.0.1"], ["2606:4700:4700::1111", "::1"]])
async def test_dns_all_answers_checked(dns, answers):
    dns.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in answers
    ]
    with pytest.raises(destination.UnsafeWebhookError):
        await destination.resolve_webhook(httpx.URL("https://example.com"))


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [socket.gaierror(), TimeoutError()])
async def test_dns_failure_closed(dns, error):
    dns.side_effect = error
    with pytest.raises(destination.UnsafeWebhookError):
        await destination.resolve_webhook(httpx.URL("https://example.com"))


@pytest.mark.asyncio
async def test_register_clear_and_secret(service, dns):
    result = await service.update_webhook(
        "agent", AgentWebhookRequest(url="https://example.com/hook", secret="synthetic")
    )
    service.repo.update_webhook.assert_awaited_once_with(
        "agent", result["webhook_url"], "synthetic"
    )
    dns.reset_mock()
    assert await service.update_webhook("agent", AgentWebhookRequest()) == {
        "webhook_url": None,
        "webhook_secret": None,
    }
    dns.assert_not_called()


@pytest.mark.asyncio
async def test_rejected_registration_has_no_persistence(service, dns):
    dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    with pytest.raises(HTTPException) as error:
        await service.update_webhook("agent", AgentWebhookRequest(url="https://internal.example"))
    assert error.value.status_code == 400
    service.repo.update_webhook.assert_not_called()


@pytest.mark.asyncio
async def test_route_validation_and_persistence(dns):
    app = FastAPI()
    app.include_router(router)
    db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_agent_by_api_key] = lambda: {"id": "agent"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.patch("/agents/me/webhook", json={"url": 123})).status_code == 422
        assert (
            await client.patch("/agents/me/webhook", json={"url": "http://localhost"})
        ).status_code == 400
        response = await client.patch(
            "/agents/me/webhook", json={"url": "https://example.com", "secret": "synthetic"}
        )
    assert response.status_code == 200
    db.commit.assert_awaited_once()
    assert db.execute.call_args.args[1] == {
        "id": "agent",
        "url": "https://example.com",
        "secret": "synthetic",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 302, 500])
async def test_transport_pins_ip_preserves_tls_and_never_reads_body(monkeypatch, dns, status):
    response = MagicMock(status_code=status, aclose=AsyncMock())
    transport = AsyncMock()
    transport.__aenter__.return_value = transport
    transport.handle_async_request.return_value = response
    factory = MagicMock(return_value=transport)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", factory)
    assert (
        await destination.post_webhook("https://example.com:8443/hook?q=synthetic", b"payload", {})
        == status
    )
    request = transport.handle_async_request.call_args.args[0]
    assert str(request.url) == "https://8.8.8.8:8443/hook?q=synthetic"
    assert request.headers["host"] == "example.com:8443"
    assert request.extensions["sni_hostname"] == "example.com"
    assert request.content == b"payload"
    factory.assert_called_once_with(trust_env=False, retries=0)
    response.aclose.assert_awaited_once()
    response.aread.assert_not_called()
    dns.assert_awaited_once()
    transport.handle_async_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_stored_url_revalidated_on_every_attempt(dns, monkeypatch):
    dns.side_effect = [
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    ]
    transport = AsyncMock()
    transport.__aenter__.return_value = transport
    transport.handle_async_request.return_value = MagicMock(status_code=200, aclose=AsyncMock())
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", MagicMock(return_value=transport))
    await destination.post_webhook("https://example.com", b"", {})
    with pytest.raises(destination.UnsafeWebhookError):
        await destination.post_webhook("https://example.com", b"", {})
    transport.handle_async_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_delivery_diagnostics_exclude_exception_content(monkeypatch):
    module = "app.services.agent_webhook_service"
    db = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = db
    monkeypatch.setattr(f"{module}.async_session_maker", lambda: context)
    monkeypatch.setattr(f"{module}.use_agent_context", lambda **kw: AsyncMock())
    monkeypatch.setattr(
        AgentWebhookService,
        "fetch_webhook",
        AsyncMock(
            return_value={"url": "https://example.com", "secret": "synthetic", "failures": 0}
        ),
    )
    monkeypatch.setattr(
        f"{module}.post_webhook", AsyncMock(side_effect=ValueError("PRIVATE_RESPONSE_AND_URL"))
    )
    monkeypatch.setattr(f"{module}.asyncio.sleep", AsyncMock())
    record = AsyncMock()
    dead_letter = AsyncMock()
    monkeypatch.setattr(AgentWebhookService, "_record_failure", record)
    monkeypatch.setattr(AgentWebhookService, "_dead_letter", dead_letter)
    assert not await AgentWebhookService.deliver("agent", {"type": "test"})
    assert dead_letter.call_args.args[-1] == "ValueError"
    record.assert_awaited_once_with(db, "agent", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,host",
    [
        ("https://example.com:8443/hook", "example.com"),
        ("https://bücher.example:8443/hook", "xn--bcher-kva.example"),
        ("https://[2606:4700:4700::1111]:8443/hook", "2606:4700:4700::1111"),
    ],
)
async def test_actual_httpcore_connect_and_certificate_hostname(monkeypatch, dns, url, host):
    stream = MagicMock()
    stream.start_tls = AsyncMock(return_value=stream)
    stream.write = AsyncMock()
    stream.read = AsyncMock(
        return_value=b"HTTP/1.1 302 Found\r\nContent-Length: 999999\r\nLocation: http://127.0.0.1/\r\n\r\n"
    )
    stream.aclose = AsyncMock()
    stream.get_extra_info.return_value = None
    connect = AsyncMock(return_value=stream)
    monkeypatch.setattr("httpcore._backends.auto.AutoBackend.connect_tcp", connect)
    assert await destination.post_webhook(url, b"payload", {}) == 302
    assert connect.call_args.kwargs["host"] == "8.8.8.8"
    assert connect.call_args.kwargs["port"] == 8443
    tls = stream.start_tls.call_args.kwargs
    assert tls["server_hostname"] == host
    assert tls["ssl_context"].check_hostname
    assert tls["ssl_context"].verify_mode == ssl.CERT_REQUIRED
    assert httpx.URL(url).netloc in stream.write.call_args_list[0].args[0]
    stream.read.assert_awaited_once()
    dns.assert_awaited_once()
    connect.assert_awaited_once()

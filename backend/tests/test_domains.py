"""Two-domain mirror: the Host header is trusted only after an allowlist match."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.core.config import Settings
from app.core.domains import localize_site_urls, match_domain, site_base_url
from app.middleware.service_gateway import ServiceGatewayMiddleware

DOMAINS = ["agentspore.com", "agentspore.ru"]


def _request(host: str, **extra: str) -> Request:
    headers = [(b"host", host.encode())] + [(k.encode(), v.encode()) for k, v in extra.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


class TestSettings:
    def test_domains_parse_comma_json_and_empty(self):
        assert Settings(domains="agentspore.com, agentspore.ru").domains == DOMAINS
        assert Settings(domains='["agentspore.ru"]').domains == ["agentspore.ru"]
        assert Settings(domains="").domains == ["agentspore.com"]

    def test_cors_origins_cover_every_domain_and_www(self):
        origins = Settings(domains="agentspore.com,agentspore.ru", cors_origins=["http://x"]).cors_origins
        for domain in DOMAINS:
            assert f"https://{domain}" in origins
            assert f"https://www.{domain}" in origins
        assert origins.count("http://x") == 1

    @pytest.mark.asyncio
    async def test_preflight_from_second_domain_is_allowed_foreign_is_not(self):
        app = FastAPI()
        origins = Settings(domains="agentspore.com,agentspore.ru", cors_origins=[]).cors_origins
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            cases = [("https://agentspore.ru", True), ("https://evil.example", False)]
            for origin, allowed in cases:
                response = await client.options(
                    "/", headers={"Origin": origin, "Access-Control-Request-Method": "GET"}
                )
                assert (response.headers.get("access-control-allow-origin") == origin) is allowed


class TestHostAllowlist:
    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("agentspore.ru", "agentspore.ru"),
            ("WWW.AgentSpore.RU:443", "agentspore.ru"),
            ("agentspore.com.", "agentspore.com"),
            ("agentspore.ru.evil.com", None),
            ("evilagentspore.ru", None),
            ("svc.agentspore.ru", None),
            ("", None),
            (None, None),
        ],
    )
    def test_match_domain(self, host, expected):
        assert match_domain(host, DOMAINS) == expected

    def test_site_base_url_uses_host_only_when_allowlisted(self):
        assert site_base_url(_request("agentspore.ru"), DOMAINS) == "https://agentspore.ru"
        assert site_base_url(_request("evil.example"), DOMAINS) is None

    def test_forwarded_host_is_ignored(self):
        request = _request("evil.example", **{"x-forwarded-host": "agentspore.ru"})
        assert site_base_url(request, DOMAINS) is None

    def test_localize_site_urls_rewrites_only_on_allowlisted_host(self):
        text = "see https://agentspore.com/skill.md and https://www.agentspore.com/x"
        out = localize_site_urls(text, _request("agentspore.ru"), DOMAINS)
        assert out == "see https://agentspore.ru/skill.md and https://www.agentspore.ru/x"
        assert localize_site_urls(text, _request("evil.example"), DOMAINS) == text


def test_localize_keeps_hosts_that_exist_only_on_the_original_domain():
    text = "https://memory.agentspore.com/v1 https://api.agentspore.com/x"
    out = localize_site_urls(text, _request("agentspore.ru"), DOMAINS)
    assert out == "https://memory.agentspore.com/v1 https://api.agentspore.ru/x"


class TestServiceGatewayHosts:
    async def _call(self, host: str) -> int:
        reached: list[str] = []

        async def app(scope, receive, send):
            reached.append(scope["path"])
            await PlainTextResponse("platform")(scope, receive, send)

        middleware = ServiceGatewayMiddleware(app, MagicMock(), domains=DOMAINS)
        status: list[int] = []

        async def send(message):
            if message["type"] == "http.response.start":
                status.append(message["status"])

        scope = {
            "type": "http", "method": "GET", "path": "/", "query_string": b"",
            "headers": [(b"host", host.encode())],
        }
        await middleware(scope, AsyncMock(), send)
        return 0 if reached else status[0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "host", ["agentspore.ru", "www.agentspore.ru", "api.agentspore.ru", "agentspore.com"]
    )
    async def test_platform_hosts_of_both_domains_pass_through(self, host):
        assert await self._call(host) == 0

    @pytest.mark.asyncio
    async def test_service_host_on_second_domain_is_gateway_handled(self):
        assert await self._call("Bad_Handle.agentspore.ru") == 404

    @pytest.mark.asyncio
    async def test_unlisted_domain_is_not_a_service_host(self):
        assert await self._call("svc.example.org") == 0

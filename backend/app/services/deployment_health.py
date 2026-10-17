"""Bounded, cookieless health probes for canonical trusted service revisions."""

import asyncio
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from urllib.request import Request

import httpx

from app.schemas.deployments import DeploymentLease


class RejectHealthCookies(DefaultCookiePolicy):
    """Reject upstream cookie storage before any state enters the client jar."""

    def set_ok(self, cookie: Cookie, request: Request) -> bool:
        """Untrusted service responses may never retain client state."""
        return False


class DeploymentHealthProbe:
    """Own a direct HTTP client; callers explicitly close its transport."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.client = httpx.AsyncClient(
            transport=transport,
            cookies=CookieJar(policy=RejectHealthCookies()),
            trust_env=False,
            follow_redirects=False,
            timeout=1.8,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )

    async def __call__(self, lease: DeploymentLease) -> int:
        """Return only status or zero; never read a response body or forward cookies."""
        request = httpx.Request("GET", f"http://svc-{lease.deployment_id.hex}:8080/health")
        try:
            async with asyncio.timeout(1.8):
                response = await self.client.send(
                    request, stream=True, auth=None, follow_redirects=False
                )
        except (TimeoutError, OSError, httpx.TransportError):
            return 0
        try:
            async with asyncio.timeout(0.2):
                await response.aclose()
        except (TimeoutError, OSError, httpx.TransportError):
            return 0
        return response.status_code

    async def aclose(self) -> None:
        """Bound transport shutdown without swallowing caller cancellation."""
        try:
            async with asyncio.timeout(0.2):
                await self.client.aclose()
        except (TimeoutError, OSError, httpx.TransportError):
            return

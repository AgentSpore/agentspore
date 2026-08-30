"""Reactive proxy fallback transport for outbound LLM calls.

One proxy in ``LLM_PROXY_URL`` means a single degraded proxy stalls every
hosted agent until someone swaps the address by hand (measured 2026-08-30:
82 ``ModelAPIError('Connection error.')`` failures in a day). Speed does not
predict survival — a proxy that scores 8/8 back-to-back can score 3/6 spaced
30s apart, and its replacement the opposite — so the chain advances on
connection-level failure, not on latency or a background health check.

``ProxyChainTransport`` wraps one ``httpx.AsyncHTTPTransport`` per configured
proxy and is installed as a single ``httpx.AsyncClient``'s transport, so the
client built once per agent session (``build_llm_http_client``) can recover
mid-session without a restart. It remembers the last-good index so a healthy
proxy is not re-tried after a dead one on every request.
"""

from __future__ import annotations

import httpx
from loguru import logger


def _mask(proxy_url: str) -> str:
    """host:port only — no credentials survive into a log line."""
    parsed = httpx.URL(proxy_url)
    return f"{parsed.scheme}://{parsed.host}:{parsed.port}" if parsed.host else "<invalid>"


class ProxyChainTransport(httpx.AsyncBaseTransport):
    """Tries each proxy in order, starting from the last one that worked.

    A transport-level failure (connect error, timeout, protocol error)
    advances to the next proxy for that request. An HTTP response — even an
    error status from the model provider — is not a transport failure and is
    returned as-is without advancing the chain.
    """

    def __init__(self, proxy_urls: list[str]) -> None:
        if not proxy_urls:
            raise ValueError("ProxyChainTransport requires at least one proxy URL")
        self._proxy_urls = proxy_urls
        self._transports = [httpx.AsyncHTTPTransport(proxy=url) for url in proxy_urls]
        self._current = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last_exc: Exception | None = None
        for offset in range(len(self._transports)):
            index = (self._current + offset) % len(self._transports)
            try:
                response = await self._transports[index].handle_async_request(request)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                logger.warning(
                    "Proxy chain: '{}' failed ({}) — trying '{}'",
                    _mask(self._proxy_urls[index]),
                    type(exc).__name__,
                    _mask(self._proxy_urls[(index + 1) % len(self._transports)]),
                )
                continue

            if index != self._current:
                logger.info(
                    "Proxy chain: now using '{}'",
                    _mask(self._proxy_urls[index]),
                )
            self._current = index
            return response

        assert last_exc is not None
        raise last_exc

    async def aclose(self) -> None:
        for transport in self._transports:
            await transport.aclose()

"""Public HTTPS destinations pinned to validated DNS answers for webhook delivery."""

import asyncio
import ipaddress
import socket

import httpx


class UnsafeWebhookError(ValueError):
    """The destination cannot be used for public webhook delivery."""


def parse_webhook_url(value: str) -> httpx.URL:
    if not value or len(value) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise UnsafeWebhookError("invalid webhook URL")
    try:
        url = httpx.URL(value)
        if (
            url.scheme != "https"
            or not url.host
            or url.userinfo
            or url.fragment
            or "\\" in value
            or "%" in url.host
            or (url.port is not None and not 1 <= url.port <= 65535)
        ):
            raise UnsafeWebhookError("public HTTPS webhook URL required")
        return url
    except (httpx.InvalidURL, ValueError) as exc:
        raise UnsafeWebhookError("public HTTPS webhook URL required") from exc


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    # Explicit exclusions keep Python 3.12 and newer ipaddress policy aligned.
    blocked = (
        "192.0.0.0/24",
        "192.88.99.0/24",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2001::/23",
        "2002::/16",
    )
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_loopback
        and not address.is_link_local
        and not getattr(address, "ipv4_mapped", None)
        and not any(address in ipaddress.ip_network(net) for net in blocked)
    )


async def resolve_webhook(url: httpx.URL) -> str:
    try:
        answers = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(
                url.host,
                url.port or 443,
                type=socket.SOCK_STREAM,
            ),
            timeout=5.0,
        )
        addresses = [str(item[4][0]) for item in answers]
        if not addresses or not all(public_address(address) for address in addresses):
            raise UnsafeWebhookError("non-public webhook destination")
        return addresses[0]
    except (OSError, TimeoutError, ValueError) as exc:
        raise UnsafeWebhookError("webhook destination unavailable") from exc


async def post_webhook(url_value: str, payload: bytes, headers: dict[str, str]) -> int:
    url = parse_webhook_url(url_value)
    address = await resolve_webhook(url)
    request = httpx.Request(
        "POST",
        url.copy_with(host=address),
        content=payload,
        headers={**headers, "Host": url.netloc.decode("ascii")},
        extensions={
            "sni_hostname": url.raw_host.decode("ascii"),
            "timeout": {key: 10.0 for key in ("connect", "read", "write", "pool")},
        },
    )
    async with httpx.AsyncHTTPTransport(trust_env=False, retries=0) as transport:
        response = await transport.handle_async_request(request)
        try:
            return response.status_code
        finally:
            await response.aclose()

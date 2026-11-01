"""Refuse every request whose TCP peer sits in the A4 tenant network pool."""

import ipaddress

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings

# INVARIANT(A4-P4a): the verdict uses scope["client"] only, never X-Forwarded-For,
# Forwarded or X-Real-IP. uvicorn rewrites scope["client"] from those headers for peers
# in FORWARDED_ALLOW_IPS; that list must never include A4_TENANT_POOL, or a tenant could
# claim any address and bypass this guard. Removing the guard exposes the whole API,
# /health and /metrics to tenant code.


class A4PeerGuardMiddleware:
    """Pure ASGI, outermost: 403 (websocket: close before accept) for tenant peers."""

    def __init__(
        self, app: ASGIApp, pool: ipaddress.IPv4Network | ipaddress.IPv6Network | None = None
    ):
        self.app = app
        self.pool = pool or get_settings().a4_tenant_pool

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"} and self._is_tenant(scope.get("client")):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                }
            )
            await send({"type": "http.response.body", "body": b"Forbidden"})
            return
        await self.app(scope, receive, send)

    def _is_tenant(self, client: tuple[str, int] | None) -> bool:
        try:
            address = ipaddress.ip_address(client[0]) if client else None
        except ValueError:
            return False
        if address is None:
            return False
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address in self.pool

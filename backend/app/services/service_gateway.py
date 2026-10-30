"""Bounded service forwarding and privacy-preserving gateway usage policy."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from app.core.security import decode_token
from app.repositories.service_route_repo import ServiceRouteRepository
from app.schemas.usage import GatewayVisit, ServiceEvent, ServiceRoute
from app.services.usage_service import UsageService


class ServiceGateway:
    """Keep registry identity, authenticated owner exclusion and forwarding policy together."""

    COOKIE = "__Host-visitor"
    COOKIE_SECONDS = 691200
    UPSTREAM_SECONDS = 10
    RESPONSE_LIMIT = 2 * 1024 * 1024
    HEADER_LIMIT = 32768
    REQUEST_HEADERS = frozenset(
        {
            "accept",
            "accept-language",
            "content-type",
            "user-agent",
            "range",
        }
    )
    RESPONSE_HEADERS = frozenset(
        {
            "content-type",
            "cache-control",
            "etag",
            "last-modified",
            "content-language",
            "content-disposition",
            "vary",
            "accept-ranges",
            "content-range",
            "content-security-policy",
            "x-content-type-options",
            "referrer-policy",
            "permissions-policy",
        }
    )

    def __init__(self, db: AsyncSession):
        self.routes = ServiceRouteRepository(db)
        self.usage = UsageService(db)

    async def resolve(self, handle: str) -> ServiceRoute:
        """Resolve a registered service without falling through to platform routes."""
        route = await self.routes.get_by_handle(handle)
        if route is None:
            raise HTTPException(status_code=404, detail="Service not found")
        return route

    async def record_event(
        self, request: Request, route: ServiceRoute, event: ServiceEvent
    ) -> None:
        """Use registry scope and verified owner metadata, without creating visitor identity."""
        await self.usage.record_event(
            route.project_id,
            event,
            request.headers.get("user-agent", "")[:4096],
            self._verified_user(request),
        )

    @staticmethod
    def _verified_user(request: Request) -> UUID | None:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        try:
            payload = decode_token(token)
            return UUID(payload.sub) if payload and payload.type == "access" else None
        except (ValueError, TypeError):
            return None

    @classmethod
    def visit(cls, request: Request) -> GatewayVisit:
        """Use only a valid visitor UUID and the current platform-verified bearer identity."""
        try:
            visitor_id = UUID(request.cookies.get(cls.COOKIE, ""))
            new_cookie = False
        except (ValueError, TypeError, AttributeError):
            visitor_id, new_cookie = uuid4(), True
        return GatewayVisit(
            visitor_id=visitor_id,
            verified_user_id=cls._verified_user(request),
            user_agent=request.headers.get("user-agent", "")[:4096],
            method=request.method,
            accept=request.headers.get("accept", "")[:4096],
            path=request.url.path,
            new_cookie=new_cookie,
        )

    @classmethod
    async def forward(
        cls,
        request: Request,
        body: bytes,
        route: ServiceRoute,
        client_factory: Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]],
    ) -> Response:
        """Forward only to the UUID-derived internal service, with strict size and time bounds."""
        target = httpx.URL(
            scheme="http",
            host="svc-" + route.active_revision.hex,
            port=8080,
            path=request.url.path,
            query=request.scope.get("query_string", b""),
        )
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() in cls.REQUEST_HEADERS
        }
        headers["accept-encoding"] = "identity"
        try:
            async with asyncio.timeout(cls.UPSTREAM_SECONDS), client_factory() as client:
                async with client.stream(
                    request.method, target, content=body, headers=headers, follow_redirects=False
                ) as upstream:
                    if 300 <= upstream.status_code < 400:
                        raise HTTPException(status_code=502, detail="Service redirect refused")
                    if (
                        len(upstream.headers.raw) > 64
                        or sum(len(key) + len(value) for key, value in upstream.headers.raw)
                        > cls.HEADER_LIMIT
                        or upstream.headers.get("content-encoding", "identity").lower()
                        not in {"", "identity"}
                    ):
                        raise HTTPException(status_code=502, detail="Service headers rejected")
                    payload = bytearray()
                    async for chunk in upstream.aiter_bytes():
                        if len(payload) + len(chunk) > cls.RESPONSE_LIMIT:
                            raise HTTPException(
                                status_code=502, detail="Service response too large"
                            )
                        payload.extend(chunk)
                    forwarded = {
                        key: value
                        for key, value in upstream.headers.items()
                        if key.lower() in cls.RESPONSE_HEADERS
                    }
                    return Response(
                        bytes(payload), status_code=upstream.status_code, headers=forwarded
                    )
        except (TimeoutError, httpx.TimeoutException):
            raise HTTPException(status_code=504, detail="Service timed out") from None
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="Service unavailable") from None

    @staticmethod
    def is_html(response: Response) -> bool:
        """Only successful HTML responses qualify as service visitor traffic."""
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        return 200 <= response.status_code < 300 and content_type == "text/html"

    async def record(self, route: ServiceRoute, visit: GatewayVisit, response: Response) -> bool:
        """Keep unsuccessful and non-HTML responses out of the real usage producer."""
        if not self.is_html(response):
            return False
        return await self.usage.record_visit(route.project_id, visit)

    @staticmethod
    async def remember_address(redis: Redis, address: str, route: ServiceRoute) -> None:
        """Store a domain/day-separated HMAC only in Redis, without extending its TTL."""
        await UsageService.remember_address(redis, address, route.handle)

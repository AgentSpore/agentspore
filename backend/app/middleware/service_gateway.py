"""Intercept service origins before any platform route, middleware, or websocket handler."""

import asyncio
import re

from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import State
from starlette.requests import ClientDisconnect, Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.deps import client_ip
from app.core.config import get_settings
from app.schemas.usage import GatewayVisit, ServiceEvent, ServiceRoute
from app.services.service_gateway import ServiceGateway


class ServiceGatewayMiddleware:
    """Keep service hosts isolated and acquire database connections only after bounded input."""

    PLATFORM_LABELS = ("", "www.", "api.")
    HANDLE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)
    BODY_LIMIT = 256 * 1024
    EVENTS_PATH = "/_agentspore/events"
    EVENT_LIMIT = 512

    def __init__(
        self,
        app: ASGIApp,
        resources: State,
        domains: list[str] | None = None,
        serve_enabled: bool | None = None,
    ):
        self.app = app
        self.resources = resources
        self._serve_enabled = serve_enabled
        self.domains = domains or get_settings().domains
        self.platform_hosts = frozenset(
            label + domain for domain in self.domains for label in self.PLATFORM_LABELS
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        request = Request({**scope, "type": "http"}, receive=receive)
        host = request.headers.get("host", "").partition(":")[0].lower().rstrip(".")
        suffix = next((f".{d}" for d in self.domains if host.endswith(f".{d}")), None)
        if host in self.platform_hosts or suffix is None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        try:
            handle = host.removesuffix(suffix)
            if not self.HANDLE.fullmatch(handle):
                raise HTTPException(status_code=404, detail="Service not found")
            is_event = request.method == "POST" and request.url.path == self.EVENTS_PATH
            body = await self._body(request, self.EVENT_LIMIT if is_event else self.BODY_LIMIT)
            route = await self._resolve(handle)
            await self._require_served(route)
            if is_event:
                response = await self._event(request, route, body)
                await response(scope, receive, send)
                return
            visit = ServiceGateway.visit(request)
            response = await ServiceGateway.forward(
                request,
                body,
                route,
                self.resources.service_gateway_client_factory,
            )
            await self._record(request, route, visit, response)
            if visit.new_cookie and ServiceGateway.is_html(response):
                response.set_cookie(
                    ServiceGateway.COOKIE,
                    str(visit.visitor_id),
                    max_age=ServiceGateway.COOKIE_SECONDS,
                    path="/",
                    secure=True,
                    httponly=True,
                    samesite="lax",
                )
        except ClientDisconnect:
            return
        except HTTPException as error:
            response = PlainTextResponse(error.detail, status_code=error.status_code)
        except (SQLAlchemyError, TimeoutError):
            response = PlainTextResponse("Service unavailable", status_code=503)
        await response(scope, receive, send)

    async def _body(self, request: Request, limit: int = BODY_LIMIT) -> bytes:
        headers = request.scope.get("headers", [])
        if len(headers) > 64 or sum(len(key) + len(value) for key, value in headers) > 32768:
            raise HTTPException(status_code=431, detail="Request headers too large")
        if (
            len(request.headers.getlist("host")) != 1
            or len(request.url.path) > 8192
            or any(ord(character) < 32 or ord(character) == 127 for character in request.url.path)
            or len(request.method) > 16
        ):
            raise HTTPException(status_code=400, detail="Invalid service request")
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > limit:
                        raise HTTPException(status_code=413, detail="Request body too large")
                    body.extend(chunk)
        except TimeoutError:
            raise HTTPException(status_code=408, detail="Request body timed out") from None
        return bytes(body)

    async def _event(self, request: Request, route: ServiceRoute, body: bytes) -> Response:
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            raise HTTPException(status_code=415, detail="JSON required")
        try:
            event = ServiceEvent.model_validate_json(body)
        except ValidationError as error:
            kind = error.errors(include_input=False, include_context=False)[0]["type"]
            raise HTTPException(
                status_code=400 if kind == "json_invalid" else 422, detail="Invalid service event"
            ) from None
        async with asyncio.timeout(2), self.resources.service_gateway_db_factory.begin() as db:
            await ServiceGateway(db).record_event(request, route, event)
        return Response(status_code=204)

    @property
    def serve_enabled(self) -> bool:
        """Pinned value when given, else the live setting so a settings change needs no restart."""
        if self._serve_enabled is not None:
            return self._serve_enabled
        return get_settings().a4_serve_enabled

    async def _require_served(self, route: ServiceRoute) -> None:
        """A4_SERVE_ENABLED=false refuses every route except platform-owned ones."""
        if not self.serve_enabled and not await self._is_platform_service(route):
            raise HTTPException(status_code=503, detail="serve_disabled")

    async def _is_platform_service(self, route: ServiceRoute) -> bool:
        """Internal-agent routes are exempt from A4_SERVE_ENABLED; a missing creator is not."""
        async with asyncio.timeout(2), self.resources.service_gateway_db_factory() as db:
            row = await db.execute(
                text(
                    "SELECT a.agent_type = 'internal' FROM projects p "
                    "JOIN agents a ON a.id = p.creator_agent_id WHERE p.id = :id"
                ),
                {"id": route.project_id},
            )
            return bool(row.scalar())

    async def _resolve(self, handle: str) -> ServiceRoute:
        async with asyncio.timeout(2), self.resources.service_gateway_db_factory() as db:
            return await ServiceGateway(db).resolve(handle)

    async def _record(
        self,
        request: Request,
        route: ServiceRoute,
        visit: GatewayVisit,
        response: Response,
    ) -> None:
        try:
            async with asyncio.timeout(2):
                async with self.resources.service_gateway_db_factory.begin() as db:
                    counted = await ServiceGateway(db).record(route, visit, response)
                if counted:
                    redis = await self.resources.service_gateway_redis_factory()
                    await ServiceGateway.remember_address(redis, client_ip(request), route)
        except (SQLAlchemyError, RedisError, TimeoutError, RuntimeError, OSError):
            logger.warning("Service usage telemetry unavailable")

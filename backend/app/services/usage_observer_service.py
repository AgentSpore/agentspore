"""Authenticate a scoped producer and reuse committed visitor accounting policy."""

import asyncio
import hashlib
import hmac
from uuid import UUID

from fastapi import HTTPException, Request
from loguru import logger
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.schemas.usage import GatewayVisit
from app.schemas.usage_observer import UsageObservation, UsageObserverTarget, UsageServiceName
from app.services.usage_service import UsageService


class UsageObserverService:
    """Own one request's trusted scope and transient post-commit address observation."""

    def __init__(
        self, usage: UsageService, redis: Redis, target: UsageObserverTarget, user_id: UUID | None
    ):
        self.usage, self.redis, self.target, self.user_id = usage, redis, target, user_id
        self.pending_address: str | None = None

    @staticmethod
    def target_for(settings: Settings, service: UsageServiceName) -> UsageObserverTarget:
        """Resolve one fixed server-owned binding and reject shared producer keys."""
        bindings = {
            "otkrytka": (settings.otkrytka_usage_project_id, settings.otkrytka_usage_observer_key),
            "podmemory": (
                settings.podmemory_usage_project_id,
                settings.podmemory_usage_observer_key,
            ),
            "pereklichka": (
                settings.pereklichka_usage_project_id,
                settings.pereklichka_usage_observer_key,
            ),
        }
        if service not in bindings:
            raise HTTPException(404, "Usage observer unavailable")
        project, key = bindings[service]
        if project is None or key is None or not key.get_secret_value():
            raise HTTPException(503, "Usage observer unavailable")
        matches = sum(
            hmac.compare_digest(key.get_secret_value().encode(), other.get_secret_value().encode())
            for _, other in bindings.values()
            if other is not None
        )
        if matches != 1:
            raise HTTPException(503, "Usage observer unavailable")
        return UsageObserverTarget(service=service, project_id=project, key=key)

    @staticmethod
    def authorize(request: Request, settings: Settings, service: UsageServiceName) -> None:
        """Authenticate the bounded producer scope without touching database dependencies."""
        pairs = request.scope["headers"]
        if len(pairs) > 32 or sum(len(k) + len(v) for k, v in pairs) > 16384:
            raise HTTPException(431, "Invalid usage observation")
        target = UsageObserverService.target_for(settings, service)
        proof = request.headers.getlist("x-usage-observer-key")
        if len(proof) != 1 or not hmac.compare_digest(
            proof[0].encode(), target.key.get_secret_value().encode()
        ):
            raise HTTPException(403, "Usage observer forbidden")

    @classmethod
    async def metadata(
        cls, request: Request, settings: Settings, service: UsageServiceName
    ) -> UsageObservation:
        """Validate empty transport and closed metadata before database dependencies."""
        cls.authorize(request, settings, service)
        pairs = request.scope["headers"]
        protected = [
            k
            for k, _ in pairs
            if k.startswith(b"x-usage-") or k in {b"authorization", b"content-length"}
        ]
        if (
            len(protected) != len(set(protected))
            or request.query_params
            or request.headers.get("transfer-encoding")
        ):
            raise HTTPException(400, "Invalid usage observation")
        if request.headers.get("content-length", "0") != "0":
            raise HTTPException(400, "Invalid usage observation")
        try:
            async with asyncio.timeout(0.2):
                async for chunk in request.stream():
                    if chunk:
                        raise HTTPException(400, "Invalid usage observation")
            observation = UsageObservation.model_validate(
                {
                    k: v
                    for k, v in request.headers.items()
                    if k.startswith("x-usage-") and k != "x-usage-observer-key"
                }
            )
            if service != "otkrytka" and observation.kind != "visit":
                raise HTTPException(400, "Invalid usage observation")
            return observation
        except (ValidationError, TimeoutError):
            raise HTTPException(400, "Invalid usage observation") from None

    async def observe(self, metadata: UsageObservation) -> GatewayVisit | None:
        """Return None for an action, or a visit for post-commit cookie delivery."""
        if await self.usage.repo.project_creator(self.target.project_id) is None:
            raise HTTPException(404, "Project not found")
        if metadata.kind == "board_created":
            if self.usage.is_browser(metadata.user_agent):
                material = b"agentspore:otkrytka:board-created:v1\0" + metadata.request_id.bytes
                receipt = hmac.new(
                    self.target.key.get_secret_value().encode(), material, hashlib.sha256
                ).digest()
                await self.usage.repo.record_action_receipt(
                    self.target.project_id, receipt, self.user_id, self.usage.today()
                )
            return None
        try:
            visitor, new_cookie = UUID(metadata.visitor_id), False
        except ValueError:
            material = (
                f"agentspore:{self.target.service}:visitor:v1\0".encode()
                + metadata.request_id.bytes
            )
            digest = hmac.new(
                self.target.key.get_secret_value().encode(), material, hashlib.sha256
            ).digest()
            visitor, new_cookie = UUID(bytes=digest[:16], version=4), True
        visit = metadata.visit(visitor, self.user_id, new_cookie)
        if await self.usage.record_visit(self.target.project_id, visit):
            self.pending_address = str(metadata.client_ip)
        return visit

    async def after_commit(self) -> None:
        """Retain the committed result and cookie if the optional address sink is unavailable."""
        if self.pending_address is None:
            return
        try:
            async with asyncio.timeout(0.2):
                await UsageService.remember_address(
                    self.redis, self.pending_address, self.target.service
                )
        except (RedisError, OSError, RuntimeError, TimeoutError):
            logger.warning("Usage observer address telemetry unavailable")
        finally:
            self.pending_address = None

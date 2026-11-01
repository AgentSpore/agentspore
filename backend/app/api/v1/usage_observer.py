"""Scoped observer boundary; operational exceptions never expose metadata or SQL parameters."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from loguru import logger
from starlette.types import Receive, Scope, Send

from app.api.deps import get_usage_observer_metadata, get_usage_observer_service
from app.schemas.usage_observer import UsageObservation
from app.services.service_gateway import ServiceGateway
from app.services.usage_observer_service import UsageObserverService


class UsageObserverRoute(APIRoute):
    """Contain deferred dependency failures before committing an HTTP acknowledgment."""

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Bound pre-commit work; run optional address telemetry after dependency commit."""
        started = False

        async def checked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                deadline.reschedule(None)
                observer = scope.get("state", {}).get("usage_observer")
                if observer is not None:
                    await observer.after_commit()
                started = True
            await send(message)

        try:
            async with asyncio.timeout(1) as deadline:
                await super().handle(scope, receive, checked_send)
        except HTTPException:
            raise
        except Exception:
            logger.warning("Usage observer unavailable")
            if started:
                return
            await JSONResponse({"detail": "Usage observer unavailable"}, status_code=503)(
                scope, receive, send
            )


router = APIRouter(prefix="/_internal/usage", route_class=UsageObserverRoute)


@router.post(
    "/{service_name}/observe",
    status_code=204,
    response_class=Response,
    summary="Observe an authenticated restored-service visit",
)
async def observe_usage(
    request: Request,
    metadata: UsageObservation = Depends(get_usage_observer_metadata),
    service: UsageObserverService = Depends(get_usage_observer_service),
) -> Response:
    """Return an empty committed acknowledgment; only a new first-party visitor gets a cookie."""
    request.state.usage_observer = service
    visit = await service.observe(metadata)
    response = Response(status_code=204)
    if visit is not None and visit.new_cookie:
        response.set_cookie(
            ServiceGateway.COOKIE,
            str(visit.visitor_id),
            max_age=ServiceGateway.COOKIE_SECONDS,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
        )
    return response

"""Funnel endpoints: anonymous visit beacon and the admin report."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi import status as http
from redis.asyncio import Redis

from app.api.deps import DatabaseSession, client_ip, get_admin_user
from app.core.config import get_settings
from app.core.redis_client import get_redis
from app.schemas.funnel import FunnelReport, VisitBeacon
from app.services.funnel_service import FunnelService

router = APIRouter(tags=["funnel"])


def get_funnel_service(redis: Redis = Depends(get_redis)) -> FunnelService:
    return FunnelService(redis)


@router.post("/funnel/visit", status_code=http.HTTP_204_NO_CONTENT, response_class=Response)
async def funnel_visit(
    body: VisitBeacon,
    request: Request,
    svc: FunnelService = Depends(get_funnel_service),
) -> Response:
    """Count one anonymous page visit (daily-unique). Always 204 unless rate limited."""
    ip, ua = client_ip(request), request.headers.get("user-agent", "")
    if not await svc.allow(ip, ua, get_settings().funnel_visit_rate_limit):
        return Response(status_code=http.HTTP_429_TOO_MANY_REQUESTS)
    if svc.page_counted(body.path):
        await svc.record_unique("visit", svc.source_for(body.utm_source, body.referrer), ip, ua)
    return Response(status_code=http.HTTP_204_NO_CONTENT)


@router.get("/admin/funnel", response_model=FunnelReport, dependencies=[Depends(get_admin_user)])
async def admin_funnel(
    db: DatabaseSession,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    svc: FunnelService = Depends(get_funnel_service),
) -> FunnelReport:
    """Per day/step/source counters and per-step totals (admin only)."""
    return await svc.report(db, days)

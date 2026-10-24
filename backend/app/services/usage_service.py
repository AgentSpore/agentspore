"""Service usage policy for authenticated gateway metadata and UTC aggregates."""

import re
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.family_training_repo import FamilyTrainingRepository
from app.repositories.usage_repo import UsageRepository
from app.schemas.usage import (
    BinderEvent,
    BinderEventGroup,
    BinderEventMetrics,
    ProjectUsage,
    ServiceEvent,
    ServiceEventGroup,
    ServiceEventMetrics,
    UsageReport,
    UsageVisit,
)


class UsageService:
    """Apply browser exclusions before any write and share the UTC clock across callers."""

    _BOTS = re.compile(
        r"bot|crawler|spider|headless|phantom|selenium|playwright|puppeteer|"
        r"googleother|google-inspectiontool|google-extended|slurp|bingpreview|"
        r"facebookexternalhit|meta-external|chatgpt-user|claude-user|perplexity-user|lighthouse|"
        r"curl|wget|httpx|python|requests|axios|go-http-client|okhttp|apache-httpclient",
        re.IGNORECASE,
    )
    _BROWSER = re.compile(r"mozilla/.*(?:chrome/|firefox/|safari/|edg/)", re.IGNORECASE)
    _PROBES = frozenset(
        {
            "/health",
            "/healthz",
            "/ready",
            "/readyz",
            "/readiness",
            "/live",
            "/livez",
            "/liveness",
            "/metrics",
            "/robots.txt",
            "/favicon.ico",
            "/api",
            "/static",
            "/assets",
        }
    )

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = UsageRepository(db)

    @staticmethod
    def today() -> date:
        """Use the current UTC wall clock, independent of transaction-start SQL NOW()."""
        return datetime.now(UTC).date()

    @classmethod
    def is_browser(cls, user_agent: str) -> bool:
        """Share the browser/bot policy without fabricating a GET visit for POST events."""
        return cls._BROWSER.search(user_agent) is not None and cls._BOTS.search(user_agent) is None

    @classmethod
    def _countable(cls, visit: UsageVisit) -> bool:
        path = visit.path.lower().rstrip("/")
        return (
            visit.method == "GET"
            and "text/html" in visit.accept.lower()
            and PurePosixPath(path).suffix.lower() in {"", ".html", ".htm"}
            and path not in cls._PROBES
            and not any(path.startswith(probe + "/") for probe in cls._PROBES)
            and cls.is_browser(visit.user_agent)
        )

    async def record_visit(self, project_id: UUID, visit: UsageVisit) -> bool:
        """Count eligible browser traffic; ingress must authenticate the optional identity."""
        if not self._countable(visit):
            return False
        return await self.repo.record_visit(project_id, visit, self.today())

    async def get_usage(self, project_id: UUID) -> UsageReport:
        """Expose aggregate observations with explicitly unverified measurement coverage."""
        result = await self.repo.get_usage_report(project_id, self.today())
        if result is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return result

    async def get_agent_usage(self, agent_id: UUID) -> list[ProjectUsage]:
        """Return only this authenticated agent's project aggregates."""
        return await self.repo.get_agent_usage(agent_id, self.today())

    async def purge_expired(self) -> int:
        """Purge retained histories while returning only the legacy deleted visit count."""
        today = self.today()
        count = await self.repo.purge_expired(today)
        await FamilyTrainingRepository(self.db).purge_expired(today)
        return count

    async def record_event(
        self, project_id: UUID, event: ServiceEvent | BinderEvent,
        user_agent: str, identity: UUID | None
    ) -> None:
        """Count eligible browser actions without persisting transient identity or headers."""
        if self.is_browser(user_agent):
            if isinstance(event, BinderEvent):
                await self.repo.record_binder_event(project_id, event, identity, self.today())
            else:
                await self.repo.record_event(project_id, event, identity, self.today())

    async def get_events(
        self, project_id: UUID, agent_id: UUID
    ) -> ServiceEventMetrics | BinderEventMetrics:
        """Expose aggregate actions only to the current project creator."""
        creator = await self.repo.project_creator(project_id)
        if creator is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if creator["creator_agent_id"] != agent_id:
            raise HTTPException(status_code=403, detail="Only project creator can read events")
        if creator.get("metrics_profile") == "emergency_binder":
            rows = await self.repo.binder_event_groups(project_id, self.today())
            return BinderEventMetrics(
                groups=[BinderEventGroup.model_validate(row) for row in rows]
            )
        rows = await self.repo.event_groups(project_id, self.today())
        return ServiceEventMetrics(groups=[ServiceEventGroup.model_validate(row) for row in rows])

"""A4 admission: author gate, kill switch, hostname candidate and status view."""

import re
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.repositories.agent_repo import AgentRepository
from app.repositories.deployment_quota import DeploymentQuotaError
from app.repositories.deployment_repo import (
    DeploymentConflictError,
    DeploymentRepository,
    get_deployment_repo,
)
from app.schemas.deployments import (
    DeploymentAccepted,
    DeploymentEnqueue,
    DeploymentRequest,
    DeploymentStatus,
    DeploymentSummary,
    QuotaLimits,
)

RESERVED_HANDLES = frozenset(
    {
        "api", "www", "admin", "mail", "app", "static", "docs", "status", "auth", "login",
        "cdn", "podmemory", "otkrytka", "pereklichka", "agentspore", "foundphone",
        "family-safety", "emergency-binder",
    }
)  # fmt: skip
_HANDLE = re.compile(r"[a-z0-9][a-z0-9-]{1,38}[a-z0-9]")
_CONFLICT_STATUS = {"project_missing": 404}


def candidate_handle(repo_url: str | None) -> str | None:
    """Project slug (repository name) if it is a valid, unreserved hostname label.

    `p-` is the fallback namespace; `--` at positions 3-4 is reserved (IDNA, `xn--`).
    """
    if not repo_url:
        return None
    slug = repo_url.rstrip("/").rsplit("/", 1)[-1].lower()
    if slug.startswith("p-") or _HANDLE.fullmatch(slug) is None or slug[2:4] == "--":
        return None
    if slug in RESERVED_HANDLES:
        return None
    return slug


class DeploymentAdmission:
    """One entry point so the author check cannot be skipped or reordered."""

    def __init__(self, db: AsyncSession, repo: DeploymentRepository):
        self.db = db
        self.repo = repo
        self.projects = AgentRepository(db)

    async def _author_project(self, project_id: UUID, agent: dict) -> dict:
        project = await self.projects.get_project_basic(
            project_id, "id, creator_agent_id, repo_url"
        )
        if not project:
            raise HTTPException(status_code=404, detail={"reason_code": "project_not_found"})
        if str(project["creator_agent_id"]) != str(agent["id"]):
            raise HTTPException(status_code=403, detail={"reason_code": "not_project_author"})
        return project

    async def submit(
        self, project_id: UUID, agent: dict, idempotency_key: UUID, body: DeploymentRequest
    ) -> DeploymentAccepted:
        """Authorize the author, honour the kill switch, then enqueue idempotently."""
        project = await self._author_project(project_id, agent)
        settings = get_settings()
        # INVARIANT(A4.4): untrusted builds stay closed until isolation ships;
        # if removed, prod accepts jobs no worker can isolate.
        if not settings.a4_deploy_enabled:
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "unavailable",
                    "reason_code": "deploy_disabled",
                    "message": "Deployment is disabled.",
                },
            )
        if not project["repo_url"]:
            raise HTTPException(status_code=409, detail={"reason_code": "repository_missing"})
        request = DeploymentEnqueue(
            project_id=project_id,
            idempotency_key=idempotency_key,
            commit_sha=body.commit_sha,
            repository_url=project["repo_url"],
            handle_candidate=candidate_handle(project["repo_url"]),
        )
        limits = QuotaLimits(platform_active=settings.a4_max_active, queue=settings.a4_queue_max)
        try:
            deployment_id = await self.repo.enqueue(request, limits)
        except DeploymentQuotaError as error:
            await self.db.rollback()
            raise HTTPException(
                status_code=429,
                detail={"reason_code": error.reason_code},
                headers={"Retry-After": str(error.retry_after)},
            ) from error
        except DeploymentConflictError as error:
            await self.db.rollback()
            code = str(error)
            raise HTTPException(
                status_code=_CONFLICT_STATUS.get(code, 409), detail={"reason_code": code}
            ) from error
        await self.db.commit()
        status = await self.repo.deployment_status(deployment_id)
        return DeploymentAccepted(deployment_id=deployment_id, status=status)

    async def status(self, project_id: UUID, agent: dict) -> DeploymentStatus:
        """Latest state, active revision, public URL and the last five deployments."""
        await self._author_project(project_id, agent)
        revision, handle, rows = await self.repo.status_view(project_id)
        latest = rows[0] if rows else None
        domain = get_settings().domains[0]
        return DeploymentStatus(
            status=latest["status"] if latest else "none",
            reason_code=latest["reason_code"] if latest else None,
            active_revision=revision,
            url=f"https://{handle}.{domain}" if revision and handle else None,
            deployments=[DeploymentSummary(**row) for row in rows],
        )


def get_deployment_admission(
    db: AsyncSession = Depends(get_db),
    repo: DeploymentRepository = Depends(get_deployment_repo),
) -> DeploymentAdmission:
    """Bind admission to the request-owned transaction."""
    return DeploymentAdmission(db, repo)

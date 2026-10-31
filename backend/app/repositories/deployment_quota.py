"""Admission quotas counted from `project_deployments.created_at`; no counter tables."""

from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.deployments import QuotaLimits

_UTC_DAY_START = "date_trunc('day', clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
_OWNER_RETRY_SECONDS = 3600
_PLATFORM_RETRY_SECONDS = 300
_QUEUE_RETRY_SECONDS = 60


class DeploymentQuotaError(Exception):
    """A ceiling is reached; the caller answers 429 with `Retry-After`."""

    def __init__(self, reason_code: str, retry_after: int):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retry_after = max(1, retry_after)


async def _agent_usage(db: AsyncSession, agent: UUID) -> RowMapping:
    return (
        (
            await db.execute(
                text(
                    f"SELECT count(*) FILTER (WHERE d.created_at >= {_UTC_DAY_START}) AS daily, "
                    "count(*) FILTER (WHERE d.created_at > clock_timestamp() - interval '7 days') "
                    "AS weekly, "
                    f"CEIL(EXTRACT(EPOCH FROM {_UTC_DAY_START} + interval '1 day' "
                    "- clock_timestamp()))::int AS daily_retry, "
                    "CEIL(EXTRACT(EPOCH FROM min(d.created_at) FILTER (WHERE d.created_at > "
                    "clock_timestamp() - interval '7 days') + interval '7 days' "
                    "- clock_timestamp()))::int AS weekly_retry "
                    "FROM project_deployments d WHERE d.creator_agent_id = :agent"
                ),
                {"agent": agent},
            )
        )
        .mappings()
        .one()
    )


async def _active_services(db: AsyncSession, project_id: UUID, who: RowMapping) -> RowMapping:
    return (
        (
            await db.execute(
                text(
                    "SELECT count(DISTINCT d.project_id) FILTER (WHERE "
                    "CASE WHEN CAST(:owner AS uuid) IS NULL THEN d.creator_agent_id = :agent "
                    "ELSE d.owner_user_id = CAST(:owner AS uuid) END) AS owned, "
                    "count(DISTINCT d.project_id) AS platform "
                    "FROM project_deployments d WHERE d.status = 'active' AND d.project_id <> :id"
                ),
                {"id": project_id, "agent": who["agent"], "owner": who["owner"]},
            )
        )
        .mappings()
        .one()
    )


async def check_quotas(db: AsyncSession, project_id: UUID, limits: QuotaLimits) -> None:
    """Raise `DeploymentQuotaError` for the first reached ceiling.

    Takes a transaction-scoped advisory lock first: the per-project row lock cannot
    serialise different projects of one agent racing for the same quota.
    """
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('a4_admission'))"))
    who = (
        (
            await db.execute(
                text(
                    "SELECT p.creator_agent_id AS agent, a.owner_user_id AS owner "
                    "FROM projects p JOIN agents a ON a.id = p.creator_agent_id WHERE p.id = :id"
                ),
                {"id": project_id},
            )
        )
        .mappings()
        .one()
    )
    agent = await _agent_usage(db, who["agent"])
    if agent["daily"] >= limits.agent_daily:
        raise DeploymentQuotaError("agent_daily_quota", agent["daily_retry"])
    if agent["weekly"] >= limits.agent_weekly:
        raise DeploymentQuotaError("agent_weekly_quota", agent["weekly_retry"] or 1)
    live = await _active_services(db, project_id, who)
    if live["owned"] >= limits.owner_active:
        raise DeploymentQuotaError("owner_active_quota", _OWNER_RETRY_SECONDS)
    if live["platform"] >= limits.platform_active:
        raise DeploymentQuotaError("platform_capacity", _PLATFORM_RETRY_SECONDS)
    queued = await db.scalar(
        text("SELECT count(*) FROM project_deployments WHERE status = 'queued'")
    )
    if queued >= limits.queue:
        raise DeploymentQuotaError("queue_full", _QUEUE_RETRY_SECONDS)

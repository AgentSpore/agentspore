"""Read cleanup candidates while excluding new work through the shared lease row."""

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession


class DeploymentCleanupRepository:
    """Keep idle cleanup coordination and image-retention queries in one transaction."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock_idle_cleanup(self) -> bool:
        """Exclude claims and pushes until the caller commits its cleanup transaction."""
        result = await self.db.execute(
            text("SELECT deployment_id FROM deployment_worker_lease WHERE slot=1 FOR UPDATE")
        )
        return result.one().deployment_id is None

    async def runtime_cleanup_rows(self) -> list[RowMapping]:
        """Read protected runtime state under the caller's idle-slot lock."""
        rows = await self.db.execute(
            text(
                "SELECT d.id, d.status, d.sandbox_namespace, d.retired_at, "
                "EXISTS (SELECT 1 FROM service_routes r WHERE r.active_revision=d.id) AS routed "
                "FROM project_deployments d WHERE d.sandbox_namespace IS NOT NULL"
            )
        )
        return list(rows.mappings().all())

    async def runtime_image_keep(self) -> list[str]:
        """Keep active/routed images and the most recently activated superseded per project."""
        rows = await self.db.execute(
            text(
                "SELECT sandbox_namespace, image_digest FROM project_deployments d "
                "WHERE status='active' OR EXISTS "
                "(SELECT 1 FROM service_routes r WHERE r.active_revision=d.id) OR id IN "
                "(SELECT DISTINCT ON (project_id) id FROM project_deployments "
                "WHERE status='superseded' ORDER BY project_id, "
                "activated_at DESC NULLS LAST, id DESC)"
            )
        )
        return [
            f"127.0.0.1:5000/{row.sandbox_namespace}@{row.image_digest}"
            for row in rows
            if row.sandbox_namespace and row.image_digest
        ]

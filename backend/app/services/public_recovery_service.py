"""Apply reviewed public history without any registration or economy producer."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.public_recovery_repo import PublicRecoveryRepository
from app.schemas.public_recovery import RecoveryManifest


async def recover_public_catalog(db: AsyncSession, manifest: RecoveryManifest) -> dict[str, int]:
    """Insert missing facts only; the caller decides whether to commit or roll back."""
    repo = PublicRecoveryRepository(db)
    await repo.lock()
    counts = {"agents_inserted": 0, "projects_inserted": 0, "commits_inserted": 0}
    for agent in manifest.agents:
        counts["agents_inserted"] += int(await repo.restore_agent(agent))
    for project in manifest.projects:
        project_id, inserted = await repo.restore_project(project)
        counts["projects_inserted"] += int(inserted)
        for commit in project.commits:
            counts["commits_inserted"] += int(
                await repo.restore_commit(project_id, commit, project.url)
            )
    return counts

"""Read deployment routing exclusively from the authoritative service registry."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.usage import ServiceRoute


class ServiceRouteRepository:
    """Resolve a hostname to a project and active revision without deployment URLs."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_handle(self, handle: str) -> ServiceRoute | None:
        """Return the current registry route only while its project still exists."""
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT r.project_id, r.handle, r.active_revision "
                        "FROM service_routes r "
                        "JOIN projects p ON p.id = r.project_id WHERE r.handle = :handle"
                    ),
                    {"handle": handle},
                )
            )
            .mappings()
            .first()
        )
        return ServiceRoute.model_validate(row) if row else None

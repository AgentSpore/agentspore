"""Training browser policy and public statistics without visitor identity."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.family_training_repo import FamilyTrainingRepository
from app.schemas.family_training import FamilyTrainingEvent, TrainingRound, TrainingStats
from app.services.usage_service import UsageService


class FamilyTrainingService:
    """Use the shared UTC clock and browser policy, retaining only bounded progress."""

    def __init__(self, db: AsyncSession):
        self.repo = FamilyTrainingRepository(db)

    async def record_progress(
        self, project_id: UUID, event: FamilyTrainingEvent, user_agent: str, identity: UUID | None
    ) -> None:
        """Exclude bots before the atomic owner-aware producer, without creating a visitor."""
        if UsageService.is_browser(user_agent):
            await self.repo.record_progress(project_id, event, identity, UsageService.today())

    async def get_stats(self, project_id: UUID, version: int) -> TrainingStats:
        """Read one UTC cohort and expose only counts and bounded ratios."""
        rows = await self.repo.aggregate(project_id, version, UsageService.today())
        starts, completions = rows[0]["starts"], rows[0]["completions"]
        return TrainingStats(
            curriculum_version=version,
            starts=starts,
            completions=completions,
            completion_ratio=completions / starts if starts else None,
            rounds=[
                TrainingRound(
                    round_id=row["round_id"],
                    answered=row["answered"],
                    wrong=row["wrong"],
                    error_ratio=row["wrong"] / row["answered"] if row["answered"] else None,
                )
                for row in rows
            ],
        )

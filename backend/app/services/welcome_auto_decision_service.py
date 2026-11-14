"""Approve undecided welcome applications that satisfy every automatic rule; never reject."""

from datetime import datetime, timedelta
from typing import Literal

from app.models.user import User
from app.repositories.welcome_auto_decision_repo import WelcomeAutoDecisionRepository
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.services.welcome_beneficiary_service import WelcomeBeneficiaryService
from app.services.welcome_reward_decision_service import WelcomeRewardDecisionService

AUTHORITY_PREFIX = "auto:"
RULE_VERSION = "v1"
AUTHORITY_REF = f"{AUTHORITY_PREFIX}{RULE_VERSION}"

Outcome = Literal["approved", "left", "capped"]


class WelcomeAutoDecisionService:
    """Record approvals through the manual decision writer, one short transaction each."""

    def __init__(
        self,
        repo: WelcomeRewardRepository,
        auto_repo: WelcomeAutoDecisionRepository,
        min_account_age_days: int,
        daily_limit: int,
    ):
        self.repo = repo
        self.auto_repo = auto_repo
        self.min_account_age_days = min_account_age_days
        self.daily_limit = daily_limit

    async def candidates(self) -> tuple[list[dict], int, int]:
        """Read-only (no program lock): prefiltered applications, undecided total, cap left."""
        used = await self.auto_repo.approvals_since_day(AUTHORITY_PREFIX)
        now = await self.repo.decision_clock()
        found = await self.auto_repo.eligible_applications(self.min_account_age_days, now)
        return found, await self.auto_repo.undecided_count(), max(self.daily_limit - used, 0)

    async def approve(self, application: dict) -> Outcome:
        """Recount the cap under the run lock, then approve one application in this transaction."""
        await self.repo.decision_clock()
        await self.repo.limit_lock_wait()
        await self.auto_repo.lock()
        if await self.auto_repo.approvals_since_day(AUTHORITY_PREFIX) >= self.daily_limit:
            return "capped"
        return "approved" if await self._record(application) else "left"

    async def _record(self, application: dict) -> bool:
        user, program, grant = await self.repo.context(application["owner_user_id"])
        if user is None or grant is None or grant["state"] != "application":
            return False
        now = await self.repo.decision_clock()
        assessment = WelcomeBeneficiaryService.evaluate(user, now)
        key = assessment.beneficiary_key
        if assessment.holds or not self._old_enough(user, grant, now):
            return False
        if await self.auto_repo.beneficiary_approved_elsewhere(key, grant["id"]):
            return False
        intent = WelcomeRewardDecisionService.allow_intent(user, program, grant, AUTHORITY_REF, now)
        result = await WelcomeRewardDecisionService(self.repo).record(intent)
        return result.verdict in {"recorded", "replayed"}

    def _old_enough(self, user: User, grant: dict, now: datetime) -> bool:
        """GitHub age is judged at application time; platform age at decision time."""
        age = timedelta(days=self.min_account_age_days)
        if WelcomeBeneficiaryService.github_bound(user):
            created = user.oauth_account_created_at
            return created is not None and grant["created_at"] - created >= age
        return user.created_at is not None and now - user.created_at >= age

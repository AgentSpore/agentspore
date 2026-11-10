"""Approve undecided welcome applications that satisfy every automatic rule; never reject."""

from datetime import timedelta

from app.models.user import User
from app.repositories.welcome_auto_decision_repo import WelcomeAutoDecisionRepository
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.welcome_reward_decisions import (
    WelcomeAutoDecisionResult,
    WelcomeRewardDecision,
)
from app.services.welcome_beneficiary_service import WelcomeBeneficiaryService
from app.services.welcome_reward_decision_service import WelcomeRewardDecisionService

RULE_VERSION = "v1"
AUTHORITY_REF = f"auto:{RULE_VERSION}"
DECISION_TTL = timedelta(days=1)


class WelcomeAutoDecisionService:
    """Record approvals through the manual decision writer under a serialized rolling cap."""

    def __init__(
        self,
        repo: WelcomeRewardRepository,
        auto_repo: WelcomeAutoDecisionRepository,
        min_account_age_days: int,
        daily_limit: int,
    ):
        self.repo = repo
        self.auto_repo = auto_repo
        self.min_account_age = timedelta(days=min_account_age_days)
        self.daily_limit = daily_limit

    async def run(self) -> WelcomeAutoDecisionResult:
        """Approve eligible applications up to the rolling cap, in the caller's transaction."""
        await self.repo.decision_clock()
        await self.repo.limit_lock_wait()
        await self.auto_repo.lock()
        remaining = self.daily_limit - await self.auto_repo.approvals_since_day(AUTHORITY_REF)
        applications = await self.auto_repo.undecided_applications()
        approved = 0
        for application in applications:
            if approved >= remaining:
                break
            approved += await self._approve(application)
        return WelcomeAutoDecisionResult(approved=approved, left=len(applications) - approved)

    async def _approve(self, application: dict) -> bool:
        user, program, grant = await self.repo.context(application["owner_user_id"])
        if user is None or grant is None or grant["state"] != "application":
            return False
        now = await self.repo.decision_clock()
        assessment = WelcomeBeneficiaryService.evaluate(user, now)
        key = assessment.beneficiary_key
        if assessment.holds or key is None or not self._old_enough(user, grant):
            return False
        if await self.auto_repo.beneficiary_approved_elsewhere(key, grant["id"]):
            return False
        intent = WelcomeRewardDecision(
            grant_id=grant["id"],
            owner_user_id=grant["owner_user_id"],
            source_event=grant["source_event"],
            policy_version=grant["tariff_version"] or program["tariff_version"],
            outcome="allow",
            beneficiary_key=key,
            authority_ref=AUTHORITY_REF,
            evidence_ref=f"{AUTHORITY_REF}:{grant['id']}:r0",
            expected_revision=0,
            email_proof_at=user.email_proof_at,
            issued_at=now,
            expires_at=now + DECISION_TTL,
        )
        result = await WelcomeRewardDecisionService(self.repo).record(intent)
        return result.verdict in {"recorded", "replayed"}

    def _old_enough(self, user: User, grant: dict) -> bool:
        created = user.oauth_account_created_at
        return created is not None and grant["created_at"] - created >= self.min_account_age

"""Approve undecided welcome applications that satisfy every automatic rule; never reject."""

from datetime import timedelta
from typing import Literal

from app.models.user import User
from app.repositories.welcome_auto_decision_repo import WelcomeAutoDecisionRepository
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.welcome_reward_decisions import WelcomeRewardDecision
from app.services.welcome_beneficiary_service import WelcomeBeneficiaryService
from app.services.welcome_reward_decision_service import WelcomeRewardDecisionService

AUTHORITY_PREFIX = "auto:"
RULE_VERSION = "v1"
AUTHORITY_REF = f"{AUTHORITY_PREFIX}{RULE_VERSION}"
DECISION_TTL = timedelta(days=1)

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
        found = await self.auto_repo.eligible_applications(self.min_account_age_days)
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
        age = timedelta(days=self.min_account_age_days)
        return created is not None and grant["created_at"] - created >= age

"""Operator review of undecided welcome applications and one-by-one approval of clean ones."""

from collections import Counter
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from app.repositories.welcome_auto_decision_repo import WelcomeAutoDecisionRepository
from app.repositories.welcome_review_repo import WelcomeReviewRepository
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.welcome_rewards import WelcomeReviewRow
from app.services.welcome_beneficiary_service import WelcomeBeneficiaryService
from app.services.welcome_progress import hold_reasons
from app.services.welcome_reward_decision_service import WelcomeRewardDecisionService

SCAN_LIMIT = 200
AUTHORITY_REF = "operator:clean-v1"


def mask_email(email: str) -> str:
    """`a***@domain`: enough to recognise the owner, not enough to contact them."""
    local, _, domain = email.rpartition("@")
    return f"{local[:1]}***@{domain}"


class WelcomeReviewService:
    def __init__(
        self,
        repo: WelcomeRewardRepository,
        review_repo: WelcomeReviewRepository,
        auto_repo: WelcomeAutoDecisionRepository,
    ):
        self.repo = repo
        self.review_repo = review_repo
        self.auto_repo = auto_repo

    async def total(self) -> int:
        """All applications still waiting for a decision."""
        return await self.auto_repo.undecided_count()

    async def rows(
        self, limit: int = SCAN_LIMIT, only: UUID | None = None
    ) -> list[WelcomeReviewRow]:
        """Oldest undecided applications with holds and duplicate matches; read-only."""
        grants = await self.review_repo.pending(limit, only)
        users = await self.review_repo.users({g["owner_user_id"] for g in grants})
        keys = {uid: WelcomeBeneficiaryService.email_key(user.email) for uid, user in users.items()}
        taken = await self.review_repo.taken_keys(set(keys.values()))
        wallets = {u.solana_wallet for u in users.values() if u.solana_wallet}
        paid = await self.review_repo.paid_wallets(wallets)
        seen = Counter(keys[g["owner_user_id"]] for g in grants)
        now = datetime.now(UTC)
        found = []
        for grant in grants:
            user = users[grant["owner_user_id"]]
            key = keys[user.id]
            found.append(
                WelcomeReviewRow(
                    grant_id=grant["grant_id"],
                    age_seconds=grant["age"],
                    holds=tuple(hold_reasons(user, now, user.solana_wallet in paid)),
                    email_match=key in taken or seen[key] > 1,
                    email_masked=mask_email(user.email),
                )
            )
        return found

    async def approve(self, grant_id: UUID) -> Literal["approved", "left"]:
        """Re-check one application under the auto-run lock and record an allow decision."""
        await self.repo.decision_clock()
        await self.repo.limit_lock_wait()
        await self.auto_repo.lock()
        found = await self.review_repo.pending(1, grant_id)
        if not found or not (await self.rows(1, grant_id))[0].clean:
            return "left"
        user, program, grant = await self.repo.context(found[0]["owner_user_id"])
        if user is None or grant is None:
            return "left"
        now = await self.repo.decision_clock()
        intent = WelcomeRewardDecisionService.allow_intent(user, program, grant, AUTHORITY_REF, now)
        result = await WelcomeRewardDecisionService(self.repo).record(intent)
        return "approved" if result.verdict in {"recorded", "replayed"} else "left"

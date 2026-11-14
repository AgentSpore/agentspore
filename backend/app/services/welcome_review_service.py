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
YOUNG_ACCOUNT = "account_too_young"


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
        min_account_age_days: int = 0,
    ):
        self.repo = repo
        self.review_repo = review_repo
        self.auto_repo = auto_repo
        self.min_account_age_days = min_account_age_days

    async def total(self) -> int:
        """All applications waiting for a decision, expired allows included."""
        return await self.review_repo.count()

    async def rows(
        self, limit: int = SCAN_LIMIT, only: UUID | None = None, now: datetime | None = None
    ) -> list[WelcomeReviewRow]:
        """Oldest waiting applications with holds and duplicate matches; read-only.

        The account-age rule of the automatic path counts as a hold, so clean is never weaker.
        """
        grants = await self.review_repo.pending(limit, only)
        users = await self.review_repo.users({g["owner_user_id"] for g in grants})
        keys = {uid: WelcomeBeneficiaryService.email_key(user.email) for uid, user in users.items()}
        taken = await self.review_repo.taken_keys(set(keys.values()))
        wallets = {u.solana_wallet for u in users.values() if u.solana_wallet}
        paid = await self.repo.welcome_intent_wallets(wallets)
        seen = Counter(keys[g["owner_user_id"]] for g in grants)
        now = now or datetime.now(UTC)
        found = []
        for grant in grants:
            user = users[grant["owner_user_id"]]
            key = keys[user.id]
            holds = hold_reasons(user, now, user.solana_wallet in paid)
            if not WelcomeBeneficiaryService.old_enough(
                user, grant["created_at"], now, self.min_account_age_days
            ):
                holds.append(YOUNG_ACCOUNT)
            found.append(
                WelcomeReviewRow(
                    grant_id=grant["grant_id"],
                    owner_user_id=grant["owner_user_id"],
                    revision=grant["revision"],
                    age_seconds=grant["age"],
                    holds=tuple(holds),
                    email_match=bool(taken.get(key, set()) - {grant["grant_id"]}) or seen[key] > 1,
                    email_masked=mask_email(user.email),
                    expired=grant["revision"] > 0,
                )
            )
        return found

    async def approve(self, grant_id: UUID) -> Literal["approved", "left"]:
        """Re-check one application under the auto-run lock; allow, superseding an expired allow."""
        await self.repo.decision_clock()
        await self.repo.limit_lock_wait()
        await self.auto_repo.lock()
        now = await self.repo.decision_clock()
        found = await self.rows(1, grant_id, now)
        if not found or not found[0].clean:
            return "left"
        user, program, grant = await self.repo.context(found[0].owner_user_id)
        if user is None or grant is None:
            return "left"
        grant = grant | {"revision": found[0].revision}
        intent = WelcomeRewardDecisionService.allow_intent(user, program, grant, AUTHORITY_REF, now)
        result = await WelcomeRewardDecisionService(self.repo).record(intent)
        return "approved" if result.verdict in {"recorded", "replayed"} else "left"

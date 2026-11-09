"""Derive the one canonical welcome beneficiary and the reasons it cannot be admitted."""

import re
from datetime import datetime, timedelta
from uuid import UUID

from disposable_email_domains import blocklist

from app.models.user import User
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.welcome_reward_decisions import BeneficiaryAssessment
from app.services.email_proof_service import EmailProofService


class WelcomeBeneficiaryService:
    """Assess recipients on the numeric GitHub id; logins, emails and wallets never qualify."""

    MIN_ACCOUNT_AGE = timedelta(days=30)
    GITHUB_ID = re.compile(r"[1-9][0-9]*")

    def __init__(self, repo: WelcomeRewardRepository):
        self.repo = repo

    async def assess(self, user_id: UUID, now: datetime) -> BeneficiaryAssessment | None:
        """Assess a stored user; None when the user does not exist."""
        user = await self.repo.user(user_id)
        return None if user is None else self.evaluate(user, now)

    @classmethod
    def evaluate(cls, user: User, now: datetime) -> BeneficiaryAssessment:
        """Return the canonical key (or None) and the ordered hold reasons."""
        holds: list[str] = []
        key = None
        if (
            user.oauth_provider == "github"
            and user.oauth_id is not None
            and cls.GITHUB_ID.fullmatch(user.oauth_id)
        ):
            key = f"github:{user.oauth_id}"
            if user.oauth_account_created_at is None:
                holds.append("github_account_age_unknown")
            elif now - user.oauth_account_created_at < cls.MIN_ACCOUNT_AGE:
                holds.append("github_account_too_new")
        else:
            holds.append("no_github_identity")
        if not EmailProofService.has_current_proof(user):
            holds.append("no_current_email_proof")
        if cls.is_disposable_domain(user.email.rpartition("@")[2]):
            holds.append("disposable_email_domain")
        return BeneficiaryAssessment(beneficiary_key=key, holds=tuple(holds))

    @staticmethod
    def is_disposable_domain(domain: str) -> bool:
        """Match the domain, its IDNA form and every parent suffix against the blocklist."""
        domain = domain.strip().strip(".").lower()
        forms = {domain}
        try:
            forms.add(domain.encode("idna").decode("ascii"))
        except UnicodeError:
            pass
        for form in forms:
            labels = form.split(".")
            if any(".".join(labels[i:]) in blocklist for i in range(len(labels) - 1)):
                return True
        return False

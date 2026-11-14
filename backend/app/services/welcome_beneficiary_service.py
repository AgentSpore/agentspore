"""Derive the one canonical welcome beneficiary and the reasons it cannot be admitted."""

import hashlib
import re
from contextlib import suppress
from datetime import datetime, timedelta
from uuid import UUID

from disposable_email_domains import blocklist

from app.models.user import User
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.welcome_reward_decisions import BeneficiaryAssessment
from app.services.email_proof_service import EmailProofService


class WelcomeBeneficiaryService:
    """Assess recipients on the hash of the normalized verified email; one key per mailbox."""

    MIN_ACCOUNT_AGE = timedelta(days=30)
    GITHUB_ID = re.compile(r"[1-9][0-9]*")
    PROVIDER_DOMAINS = {
        **dict.fromkeys(("googlemail.com",), "gmail.com"),
        **dict.fromkeys(
            ("yandex.com", "yandex.by", "yandex.kz", "yandex.ua", "yandex.com.tr", "ya.ru")
            + ("narod.ru",),
            "yandex.ru",
        ),
        **dict.fromkeys(("me.com", "mac.com"), "icloud.com"),
        **dict.fromkeys(("protonmail.com", "protonmail.ch", "pm.me"), "proton.me"),
    }
    DOT_INSENSITIVE = frozenset({"gmail.com"})
    DOT_IS_HYPHEN = frozenset({"yandex.ru"})

    def __init__(self, repo: WelcomeRewardRepository):
        self.repo = repo

    async def assess(self, user_id: UUID, now: datetime) -> BeneficiaryAssessment | None:
        """Assess a stored user; None when the user does not exist."""
        user = await self.repo.user(user_id)
        return None if user is None else self.evaluate(user, now)

    @classmethod
    def evaluate(cls, user: User, now: datetime) -> BeneficiaryAssessment:
        """Return the canonical email key and the ordered hold reasons."""
        holds: list[str] = []
        if cls.github_bound(user):
            if user.oauth_account_created_at is None:
                holds.append("github_account_age_unknown")
            elif now - user.oauth_account_created_at < cls.MIN_ACCOUNT_AGE:
                holds.append("github_account_too_new")
        if not EmailProofService.has_current_proof(user):
            holds.append("no_current_email_proof")
        if cls.is_disposable_domain(user.email.rpartition("@")[2]):
            holds.append("disposable_email_domain")
        return BeneficiaryAssessment(beneficiary_key=cls.email_key(user.email), holds=tuple(holds))

    @classmethod
    def github_bound(cls, user: User) -> bool:
        """True when the user holds a GitHub identity with a numeric id."""
        return (
            user.oauth_provider == "github"
            and user.oauth_id is not None
            and cls.GITHUB_ID.fullmatch(user.oauth_id) is not None
        )

    @classmethod
    def normalize_email(cls, email: str) -> str:
        """Collapse mailbox aliases: case, IDNA domain, `+tag` and provider domain/dot rules."""
        local, _, domain = email.strip().lower().rpartition("@")
        domain = domain.strip(".")
        with suppress(UnicodeError):
            domain = domain.encode("idna").decode("ascii")
        domain = cls.PROVIDER_DOMAINS.get(domain, domain)
        local = local.partition("+")[0]
        if domain in cls.DOT_INSENSITIVE:
            local = local.replace(".", "")
        elif domain in cls.DOT_IS_HYPHEN:
            local = local.replace(".", "-")
        return f"{local}@{domain}"

    @classmethod
    def email_key(cls, email: str) -> str:
        """Opaque beneficiary key; the raw address never reaches ledgers or logs."""
        digest = hashlib.sha256(cls.normalize_email(email).encode()).hexdigest()
        return f"email:{digest}"

    @staticmethod
    def is_disposable_domain(domain: str) -> bool:
        """Match the domain, its IDNA form and every parent suffix against the blocklist."""
        domain = domain.strip().strip(".").lower()
        forms = {domain}
        with suppress(UnicodeError):
            forms.add(domain.encode("idna").decode("ascii"))
        for form in forms:
            labels = form.split(".")
            if any(".".join(labels[i:]) in blocklist for i in range(len(labels) - 1)):
                return True
        return False

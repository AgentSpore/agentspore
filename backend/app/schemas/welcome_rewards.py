"""Public welcome status never exposes internal risk or funding evidence."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, computed_field


class WelcomeStep(BaseModel):
    id: Literal["email", "wallet", "application", "review", "payout"]
    done: bool


class WelcomePayout(BaseModel):
    state: Literal["pending", "sent", "confirmed"]
    signature: str | None = None


class WelcomeAction(BaseModel):
    """An existing endpoint the card calls inline to finish the current step."""

    id: Literal[
        "email_proof_request",
        "email_proof_confirm",
        "wallet_bind",
        "welcome_application",
        "github_relink",
    ]
    method: Literal["GET", "POST", "PATCH"]
    path: str


class WelcomeRewardView(BaseModel):
    state: Literal["disabled", "not_applied", "application", "frozen", "available", "denied"]
    reason: Literal[
        "program_disabled",
        "not_eligible",
        "not_applied",
        "risk_review",
        "funding_unavailable",
        "email_required",
        "reserved",
        "available",
        "denied",
    ]
    amount_raw: int | None = None
    decimals: Literal[6] = 6
    asset: Literal["ASPORE"] = "ASPORE"
    steps: list[WelcomeStep] = []
    hold_reasons: list[
        Literal[
            "email_not_confirmed",
            "disposable_email",
            "wallet_missing",
            "wallet_already_used",
            "github_too_new",
            "github_age_unknown",
            "other_hold",
        ]
    ] = []
    payout: WelcomePayout | None = None
    wait_until: datetime | None = None
    next_step: Literal[
        "confirm_email",
        "change_email",
        "relink_github",
        "bind_wallet",
        "apply",
        "wait_review",
        "wait_payout",
        "none",
    ] = "none"
    actions: list[WelcomeAction] = []

    @computed_field
    @property
    def next_action(self) -> str:
        return {
            "email_required": "verify_email",
            "risk_review": "await_review",
            "funding_unavailable": "await_funding",
            "program_disabled": "await_program",
        }.get(self.reason, "none")


class WelcomeReviewRow(BaseModel):
    """One pending application as the operator sees it: masked email, no keys or wallets."""

    grant_id: UUID
    age_seconds: int
    holds: tuple[str, ...]
    email_match: bool
    email_masked: str
    owner_user_id: UUID
    revision: int = 0
    expired: bool = False

    @property
    def wallet_match(self) -> bool:
        return "wallet_already_used" in self.holds

    @property
    def clean(self) -> bool:
        return not self.holds and not self.email_match

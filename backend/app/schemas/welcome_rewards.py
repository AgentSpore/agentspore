"""Public welcome status never exposes internal risk or funding evidence."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, computed_field


class WelcomeStep(BaseModel):
    id: Literal["email", "wallet", "application", "review", "payout"]
    done: bool


class WelcomePayout(BaseModel):
    state: Literal["pending", "sent", "confirmed"]
    signature: str | None = None


class WelcomeAction(BaseModel):
    """An existing endpoint the card calls inline to finish the current step."""

    id: Literal["email_proof_request", "email_proof_confirm", "wallet_bind", "welcome_application"]
    method: Literal["POST", "PATCH"]
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
        ]
    ] = []
    payout: WelcomePayout | None = None
    wait_until: datetime | None = None
    next_step: Literal[
        "confirm_email",
        "change_email",
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

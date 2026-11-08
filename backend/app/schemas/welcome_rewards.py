"""Public welcome status never exposes internal risk or funding evidence."""

from typing import Literal

from pydantic import BaseModel, computed_field


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

    @computed_field
    @property
    def next_action(self) -> str:
        return {
            "email_required": "verify_email",
            "risk_review": "await_review",
            "funding_unavailable": "await_funding",
            "program_disabled": "await_program",
        }.get(self.reason, "none")

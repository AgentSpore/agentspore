"""Turn a welcome grant, the owner and payout facts into the card's steps, holds and next action."""

from dataclasses import dataclass
from datetime import datetime

from solders.signature import Signature

from app.models.user import User
from app.schemas.welcome_rewards import (
    WelcomeAction,
    WelcomePayout,
    WelcomeRewardView,
    WelcomeStep,
)
from app.services.email_proof_service import EmailProofService
from app.services.welcome_beneficiary_service import WelcomeBeneficiaryService

HOLD_CODES = {
    "no_current_email_proof": "email_not_confirmed",
    "disposable_email_domain": "disposable_email",
    "github_account_too_new": "github_too_new",
    "github_account_age_unknown": "github_age_unknown",
}
UNMAPPED_HOLD = "other_hold"
ACTIONS = {
    "confirm_email": [
        WelcomeAction(
            id="email_proof_request", method="POST", path="/api/v1/auth/email-proof/request"
        ),
        WelcomeAction(
            id="email_proof_confirm", method="POST", path="/api/v1/auth/email-proof/confirm"
        ),
    ],
    "relink_github": [WelcomeAction(id="github_relink", method="GET", path="/api/v1/oauth/github")],
    "change_email": [
        WelcomeAction(id="email_change", method="POST", path="/api/v1/users/me/email/change"),
        WelcomeAction(
            id="email_change_confirm", method="POST", path="/api/v1/users/me/email/change/confirm"
        ),
    ],
    "bind_wallet": [
        WelcomeAction(id="wallet_bind", method="PATCH", path="/api/v1/users/solana-wallet")
    ],
    "apply": [
        WelcomeAction(
            id="welcome_application",
            method="POST",
            path="/api/v1/users/me/rewards/welcome/application",
        )
    ],
}


def payout_view(row: dict | None) -> WelcomePayout | None:
    """Pending until a signed attempt exists, then sent; confirmed after finality evidence."""
    if row is None:
        return None
    if row["signature"] is None:
        return WelcomePayout(state="pending")
    state = "confirmed" if row["confirmed"] else "sent"
    return WelcomePayout(state=state, signature=str(Signature.from_bytes(bytes(row["signature"]))))


def hold_reasons(user: User, now: datetime, wallet_taken: bool) -> list[str]:
    """User-facing hold codes in the order the owner can resolve them."""
    found = WelcomeBeneficiaryService.evaluate(user, now).holds
    codes = list(dict.fromkeys(HOLD_CODES.get(hold, UNMAPPED_HOLD) for hold in found))
    if not user.solana_wallet:
        codes.append("wallet_missing")
    elif wallet_taken:
        codes.append("wallet_already_used")
    order = [*HOLD_CODES.values(), UNMAPPED_HOLD, "wallet_missing", "wallet_already_used"]
    return sorted(codes, key=order.index)


def next_step(
    view: WelcomeRewardView, holds: list[str], payout: WelcomePayout | None, decided: bool
) -> str:
    """One action or one plain wait for every combination; terminal states have none."""
    if payout:
        return "none" if payout.state == "confirmed" else "wait_payout"
    waiting = decided or view.reason in ("reserved", "available", "funding_unavailable")
    for hold, step in (
        ("disposable_email", "change_email"),
        ("github_age_unknown", "relink_github"),
        ("email_not_confirmed", "confirm_email"),
        ("wallet_missing", "bind_wallet"),
        ("wallet_already_used", "bind_wallet"),
    ):
        if hold in holds and not (waiting and step == "change_email"):
            return step
    if view.reason == "not_applied":
        return "apply"
    return "wait_payout" if waiting else "wait_review"


@dataclass(frozen=True)
class WelcomeFacts:
    """What the card needs beyond the base view; read once per request."""

    user: User
    grant: dict | None
    decided: bool
    payout_row: dict | None
    wallet_taken: bool


def progress(view: WelcomeRewardView, facts: WelcomeFacts, now: datetime) -> WelcomeRewardView:
    """Return `view` extended with progress; the original fields are untouched."""
    user = facts.user
    if view.state in ("disabled", "denied") or view.reason == "not_eligible":
        return view.model_copy(update={"steps": steps(facts, None)})
    payout = payout_view(facts.payout_row)
    holds = [] if payout else hold_reasons(user, now, facts.wallet_taken)
    step = next_step(view, holds, payout, facts.decided)
    wait = None
    if "github_too_new" in holds and user.oauth_account_created_at:
        wait = user.oauth_account_created_at + WelcomeBeneficiaryService.MIN_ACCOUNT_AGE
    return view.model_copy(
        update={
            "steps": steps(facts, payout),
            "hold_reasons": holds,
            "payout": payout,
            "wait_until": wait,
            "next_step": step,
            "actions": ACTIONS.get(step, []),
        }
    )


def steps(facts: WelcomeFacts, payout: WelcomePayout | None) -> list[WelcomeStep]:
    """The five-step path with each step's completion."""
    user = facts.user
    return [
        WelcomeStep(id="email", done=EmailProofService.has_current_proof(user)),
        WelcomeStep(id="wallet", done=bool(user.solana_wallet) and not facts.wallet_taken),
        WelcomeStep(id="application", done=facts.grant is not None),
        WelcomeStep(id="review", done=facts.decided),
        WelcomeStep(id="payout", done=bool(payout and payout.state == "confirmed")),
    ]

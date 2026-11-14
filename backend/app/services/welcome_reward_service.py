"""Fail-closed welcome admission; never credits deposits or creates payments."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends
from loguru import logger
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.welcome_rewards import WelcomeRewardView
from app.services.email_proof_service import EmailProofService
from app.services.welcome_progress import WelcomeFacts, progress


class WelcomeRewardService:
    def __init__(self, repo: WelcomeRewardRepository):
        self.repo = repo

    @staticmethod
    def _eligible(user: User | None, program: dict) -> bool:
        return bool(
            user
            and program["admission_start"] is not None
            and user.created_at >= program["admission_start"]
            and (program["admission_end"] is None or user.created_at < program["admission_end"])
        )

    @staticmethod
    def _admitted(decision: dict | None, program: dict, grant: dict) -> bool:
        return bool(
            decision
            and decision["current"]
            and decision["outcome"] == "allow"
            and decision["beneficiary_key"]
            and decision["policy_version"] == (grant["tariff_version"] or program["tariff_version"])
            and (
                grant["beneficiary_key"] is None
                or decision["beneficiary_key"] == grant["beneficiary_key"]
            )
        )

    async def read(self, owner: UUID) -> WelcomeRewardView:
        """State plus the card's steps, holds, payout and next action; never writes."""
        view = await self._state(owner)
        user, _, grant = await self.repo.context(owner)
        if user is None:
            return view
        grant_id = grant["id"] if grant else None
        decision = await self.repo.decision(grant_id) if grant_id else None
        facts = WelcomeFacts(
            user=user,
            grant=grant,
            decided=bool(decision and decision["outcome"] == "allow"),
            payout_row=await self.repo.payout_progress(grant_id),
            wallet_taken=await self.repo.wallet_paid_to_another(user.solana_wallet, grant_id),
        )
        return progress(view, facts, datetime.now(UTC))

    async def _state(self, owner: UUID) -> WelcomeRewardView:
        """Report current eligibility without writing or exposing internal evidence."""
        user, program, grant = await self.repo.context(owner)
        amount = grant["amount_raw"] if grant else None
        if not program["enabled"]:
            return WelcomeRewardView(
                state="frozen" if amount else "disabled",
                reason="program_disabled",
                amount_raw=amount,
            )
        if grant is None:
            return WelcomeRewardView(
                state="not_applied",
                reason="not_applied" if self._eligible(user, program) else "not_eligible",
            )
        if grant["state"] == "denied":
            return WelcomeRewardView(state="denied", reason="denied")
        decision = await self.repo.decision(grant["id"])
        state = "frozen" if amount else "application"
        if decision is None or not self._admitted(decision, program, grant):
            return WelcomeRewardView(state=state, reason="risk_review", amount_raw=amount)
        if amount is None:
            reason = (
                "funding_unavailable"
                if await self.repo.free_funding() < program["amount_raw"]
                else "risk_review"
            )
            return WelcomeRewardView(state="application", reason=reason)
        return self._funded_view(user, grant, decision)

    @staticmethod
    def _funded_view(user: User | None, grant: dict, decision: dict) -> WelcomeRewardView:
        if not user or not EmailProofService.has_current_proof(user):
            return WelcomeRewardView(
                state="frozen", reason="email_required", amount_raw=grant["amount_raw"]
            )
        if decision["email_proof_at"] != user.email_proof_at:
            return WelcomeRewardView(
                state="frozen", reason="risk_review", amount_raw=grant["amount_raw"]
            )
        return WelcomeRewardView(
            state=grant["state"],
            reason="available" if grant["state"] == "available" else "reserved",
            amount_raw=grant["amount_raw"],
        )

    async def apply_onboarding(self, owner: UUID) -> None:
        """Attempt an unfunded application without aborting a usable identity transaction."""
        await self.repo.flush_identity()
        try:
            async with self.repo.onboarding_savepoint():
                if await self.repo.enabled():
                    await self.apply(owner)
        except DBAPIError as exc:
            if exc.connection_invalidated or not self.repo.transaction_usable():
                raise
            logger.warning("Welcome application unavailable; identity transaction preserved")

    async def apply(self, owner: UUID) -> WelcomeRewardView:
        """Record at most one eligible registration application, never a promise."""
        user, program, grant = await self.repo.context(owner, lock=True)
        if not program["enabled"] or grant:
            return await self.read(owner)
        if not self._eligible(user, program):
            return await self.read(owner)
        await self.repo.apply(owner)
        return await self.read(owner)

    async def advance(self, owner: UUID) -> WelcomeRewardView:
        """Reserve or unlock only with durable risk admission and sufficient backing."""
        user, program, grant = await self.repo.context(owner, lock=True)
        if not user or not program["enabled"] or not grant or grant["state"] == "denied":
            return await self._state(owner)
        decision = await self.repo.decision(grant["id"])
        if decision is None or not self._admitted(decision, program, grant):
            return await self._state(owner)
        if grant["state"] == "application":
            if (
                not self._eligible(user, program)
                or await self.repo.beneficiary_used(decision["beneficiary_key"], grant["id"])
                or await self.repo.free_funding() < program["amount_raw"]
            ):
                return await self._state(owner)
            await self.repo.freeze(grant, program, decision)
        elif (
            grant["state"] == "frozen"
            and EmailProofService.has_current_proof(user)
            and decision["email_proof_at"] == user.email_proof_at
        ):
            await self.repo.available(grant["id"])
        return await self._state(owner)


def get_welcome_reward_service(db: AsyncSession = Depends(get_db)) -> WelcomeRewardService:
    """Bind policy to the same request transaction as current-user authentication."""
    return WelcomeRewardService(WelcomeRewardRepository(db))

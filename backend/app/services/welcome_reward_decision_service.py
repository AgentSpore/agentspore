"""Append trusted operator reviews without changing reward entitlements."""

from app.models.user import User
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.welcome_reward_decisions import WelcomeRewardDecision, WelcomeRewardDecisionResult
from app.services.email_proof_service import EmailProofService


class WelcomeRewardDecisionService:
    """Serialize replay and snapshot checks in the caller's transaction."""

    def __init__(self, repo: WelcomeRewardRepository):
        self.repo = repo

    async def record(self, intent: WelcomeRewardDecision) -> WelcomeRewardDecisionResult:
        """Append one reviewed decision or return its exact historical receipt."""
        await self.repo.decision_clock()
        user, program, grant = await self.repo.context(intent.owner_user_id, lock=True)
        previous = await self.repo.decision_evidence(intent.evidence_ref)
        if previous is not None:
            return (
                WelcomeRewardDecisionResult.receipt("replayed", previous)
                if intent.matches(previous)
                else WelcomeRewardDecisionResult(verdict="conflict")
            )
        if user is None or grant is None:
            return WelcomeRewardDecisionResult(verdict="not_found")
        if grant["id"] != intent.grant_id or grant["source_event"] != intent.source_event:
            return WelcomeRewardDecisionResult(verdict="conflict")
        latest = await self.repo.decision(grant["id"])
        if intent.expected_revision != (latest["revision"] if latest else 0):
            return WelcomeRewardDecisionResult(verdict="stale")
        rejection = await self._fresh_rejection(intent, user, program, grant)
        if rejection is not None:
            return rejection
        row = await self.repo.insert_decision(intent.model_dump(exclude={"expected_revision"}))
        return WelcomeRewardDecisionResult.receipt("recorded", row)

    async def _fresh_rejection(
        self, intent: WelcomeRewardDecision, user: User, program: dict, grant: dict
    ) -> WelcomeRewardDecisionResult | None:
        now = await self.repo.decision_clock()
        if not intent.issued_at <= now < intent.expires_at:
            return WelcomeRewardDecisionResult(verdict="invalid_input")
        if intent.policy_version != (grant["tariff_version"] or program["tariff_version"]):
            return WelcomeRewardDecisionResult(verdict="stale")
        if intent.email_proof_at != user.email_proof_at or (
            intent.email_proof_at is not None and not EmailProofService.has_current_proof(user)
        ):
            return WelcomeRewardDecisionResult(verdict="stale")
        if (
            intent.outcome == "allow"
            and grant["beneficiary_key"] is not None
            and intent.beneficiary_key != grant["beneficiary_key"]
        ):
            return WelcomeRewardDecisionResult(verdict="conflict")
        return None

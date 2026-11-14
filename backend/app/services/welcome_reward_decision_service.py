"""Append trusted operator reviews without changing reward entitlements."""

from datetime import datetime, timedelta

from app.models.user import User
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.welcome_reward_decisions import WelcomeRewardDecision, WelcomeRewardDecisionResult
from app.services.email_proof_service import EmailProofService
from app.services.welcome_beneficiary_service import WelcomeBeneficiaryService

DECISION_TTL = timedelta(days=1)


class WelcomeRewardDecisionService:
    """Serialize replay and snapshot checks in the caller's transaction."""

    def __init__(self, repo: WelcomeRewardRepository):
        self.repo = repo

    @staticmethod
    def allow_intent(
        user: User, program: dict, grant: dict, authority_ref: str, now: datetime
    ) -> WelcomeRewardDecision:
        """Allow decision for one application, keyed by authority so a re-run replays."""
        return WelcomeRewardDecision(
            grant_id=grant["id"],
            owner_user_id=grant["owner_user_id"],
            source_event=grant["source_event"],
            policy_version=grant["tariff_version"] or program["tariff_version"],
            outcome="allow",
            beneficiary_key=WelcomeBeneficiaryService.email_key(user.email),
            authority_ref=authority_ref,
            evidence_ref=f"{authority_ref}:{grant['id']}:r0",
            expected_revision=0,
            email_proof_at=user.email_proof_at,
            issued_at=now,
            expires_at=now + DECISION_TTL,
        )

    async def record(self, intent: WelcomeRewardDecision) -> WelcomeRewardDecisionResult:
        """Append one reviewed decision or return its exact historical receipt."""
        await self.repo.decision_clock()
        await self.repo.limit_lock_wait()
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
        if intent.outcome == "allow":
            assessment = WelcomeBeneficiaryService.evaluate(user, now)
            if assessment.holds or assessment.beneficiary_key != intent.beneficiary_key:
                return WelcomeRewardDecisionResult(verdict="ineligible")
        return None

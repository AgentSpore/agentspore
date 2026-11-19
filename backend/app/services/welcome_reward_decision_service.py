"""Append trusted operator reviews without changing reward entitlements."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from loguru import logger

from app.models.user import User
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.welcome_reward_decisions import WelcomeRewardDecision, WelcomeRewardDecisionResult
from app.services.email_proof_service import EmailProofService
from app.services.email_service import EmailService, get_email_service
from app.services.welcome_beneficiary_service import WelcomeBeneficiaryService

DECISION_TTL = timedelta(days=1)
NOTIFIED_OUTCOMES = {"allow": True, "deny": False}


@dataclass(frozen=True)
class DecisionNotice:
    """One owner to tell about a decision; held until the decision transaction commits."""

    email: str
    approved: bool


class WelcomeRewardDecisionService:
    """Serialize replay and snapshot checks in the caller's transaction."""

    def __init__(self, repo: WelcomeRewardRepository):
        self.repo = repo
        self.notices: list[DecisionNotice] = []

    @staticmethod
    async def deliver(notices: list[DecisionNotice], mailer: EmailService | None = None) -> None:
        """Send queued notices after commit; a failure is logged and never undoes a decision."""
        mailer = mailer or get_email_service()
        for notice in notices:
            try:
                sent = await mailer.send_welcome_decision_email(notice.email, notice.approved)
            except (httpx.HTTPError, OSError):
                logger.warning("Welcome decision email failed")
                continue
            if not sent:
                logger.warning("Welcome decision email was not sent")

    def _queue_notice(
        self, intent: WelcomeRewardDecision, user: User, last_decided: str | None
    ) -> None:
        """Queue one email when the outcome differs from the last allow or deny decision."""
        if intent.outcome not in NOTIFIED_OUTCOMES or last_decided == intent.outcome:
            return
        if not (user.is_verified and user.email):
            logger.info("Welcome decision email skipped: no verified address")
            return
        self.notices.append(DecisionNotice(user.email, NOTIFIED_OUTCOMES[intent.outcome]))

    @staticmethod
    def allow_intent(
        user: User, program: dict, grant: dict, authority_ref: str, now: datetime
    ) -> WelcomeRewardDecision:
        """Allow decision for one application, keyed by authority and revision so a re-run replays.

        `grant["revision"]` (absent before the first decision) is the revision being superseded.
        """
        revision = grant.get("revision", 0)
        return WelcomeRewardDecision(
            grant_id=grant["id"],
            owner_user_id=grant["owner_user_id"],
            source_event=grant["source_event"],
            policy_version=grant["tariff_version"] or program["tariff_version"],
            outcome="allow",
            beneficiary_key=WelcomeBeneficiaryService.email_key(user.email),
            authority_ref=authority_ref,
            evidence_ref=f"{authority_ref}:{grant['id']}:r{revision}",
            expected_revision=revision,
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
        last_decided = await self.repo.last_decided_outcome(grant["id"])
        row = await self.repo.insert_decision(intent.model_dump(exclude={"expected_revision"}))
        self._queue_notice(intent, user, last_decided)
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

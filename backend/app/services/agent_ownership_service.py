"""Require human consent and current agent control before assigning ownership."""

import hmac
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.ownership_repo import AgentOwnershipRepository
from app.schemas.ownership import AgentOwnerClaimDecision, AgentOwnerClaimResponse


class AgentOwnershipService:
    """Serialize ownership and lifecycle decisions through the same agent lock."""

    def __init__(self, db: AsyncSession):
        self.repo = AgentOwnershipRepository(db)

    @staticmethod
    def _view(claim: dict) -> AgentOwnerClaimResponse:
        return AgentOwnerClaimResponse(
            **{key: claim[key] for key in AgentOwnerClaimResponse.model_fields}
        )

    async def _claim(self, claim_id: UUID) -> dict:
        claim = await self.repo.claim(claim_id)
        if claim is None:
            raise HTTPException(404, "Claim not found")
        return claim

    async def _verified(self, user_id: UUID) -> None:
        user = await self.repo.lock_user(user_id)
        if user is None or not user["is_verified"]:
            raise HTTPException(403, "Email verification required")

    @staticmethod
    def _key(agent: dict | None, key_hash: str) -> dict:
        if (
            agent is None
            or not agent["is_active"]
            or not agent["api_key_hash"]
            or not hmac.compare_digest(agent["api_key_hash"], key_hash)
        ):
            raise HTTPException(403, "Invalid agent_id or API key")
        return agent

    async def _assign(self, agent: dict, user_id: UUID, key_hash: str) -> None:
        if agent["owner_user_id"] == user_id:
            return
        if agent["owner_user_id"] is not None:
            raise HTTPException(409, "Agent already has an owner")
        if not await self.repo.assign(agent["id"], user_id, key_hash):
            raise HTTPException(409, "Agent ownership changed")

    async def create(self, agent_id: UUID, user_id: UUID) -> AgentOwnerClaimResponse:
        """Record verified human consent without assigning agent ownership."""
        agent = await self.repo.lock_agent(agent_id)
        if agent is None:
            raise HTTPException(404, "Agent not found")
        await self._verified(user_id)
        if agent["owner_user_id"] is not None:
            previous = await self.repo.find(agent_id, user_id, "claimed")
            if agent["owner_user_id"] == user_id and previous is not None:
                return self._view(previous)
            raise HTTPException(409, "Agent already has an owner")
        if not agent["is_active"] or not agent["api_key_hash"]:
            raise HTTPException(409, "Agent is unavailable for claim")
        previous = await self.repo.find(agent_id, user_id, "pending")
        if previous is not None:
            if (
                previous["status"] == "pending"
                and previous["agent_key_hash_snapshot"] == agent["api_key_hash"]
            ):
                return self._view(previous)
            await self.repo.status(
                previous["claim_id"], "expired" if previous["status"] == "expired" else "canceled"
            )
        pending, recent = await self.repo.counts(user_id)
        if pending >= 5 or recent >= 10:
            raise HTTPException(429, "Owner claim limit reached")
        claim_id = await self.repo.create(agent_id, user_id, agent["api_key_hash"])
        return self._view(await self._claim(claim_id))

    async def human(
        self, claim_id: UUID, user_id: UUID, cancel: bool = False
    ) -> AgentOwnerClaimResponse:
        """Return or cancel only the authenticated human subject intent."""
        claim = await self._claim(claim_id)
        if claim["user_id"] != user_id:
            raise HTTPException(404, "Claim not found")
        if cancel:
            await self.repo.lock_agent(claim["agent_id"])
            claim = await self._claim(claim_id)
            if claim["status"] == "pending":
                await self.repo.status(claim_id, "canceled")
                claim = await self._claim(claim_id)
        return self._view(claim)

    async def agent(self, claim_id: UUID, actor: dict) -> AgentOwnerClaimResponse:
        """Expose the intent only to its authenticated active target agent."""
        claim = await self._claim(claim_id)
        if claim["agent_id"] != actor["id"]:
            raise HTTPException(404, "Claim not found")
        return self._view(claim)

    async def decide(
        self, claim_id: UUID, actor: dict, decision: AgentOwnerClaimDecision
    ) -> AgentOwnerClaimResponse:
        """Require current independent agent control before consuming an intent."""
        claim = await self._claim(claim_id)
        if claim["agent_id"] != actor["id"]:
            raise HTTPException(404, "Claim not found")
        agent = await self.repo.lock_agent(claim["agent_id"])
        agent = self._key(agent, actor["api_key_hash"])
        claim = await self._claim(claim_id)
        if claim["user_id"] != decision.expected_user_id:
            raise HTTPException(409, "Claim human does not match")
        await self._verified(claim["user_id"])
        if (
            claim["status"] == "claimed"
            and decision.decision == "approve"
            and agent["owner_user_id"] == claim["user_id"]
        ):
            return self._view(claim)
        if claim["status"] == "expired":
            raise HTTPException(410, "Claim expired")
        if claim["status"] != "pending":
            raise HTTPException(409, "Claim is no longer pending")
        if not hmac.compare_digest(agent["api_key_hash"], claim["agent_key_hash_snapshot"]):
            raise HTTPException(409, "Agent key changed")
        status = "denied"
        if decision.decision == "approve":
            await self._assign(agent, claim["user_id"], actor["api_key_hash"])
            status = "claimed"
        if not await self.repo.status(claim_id, status):
            raise HTTPException(410, "Claim expired")
        return self._view(await self._claim(claim_id))

    async def legacy(self, agent_id: UUID, user_id: UUID, key_hash: str) -> dict:
        """Apply both current proofs without transferring an existing owner."""
        agent = await self.repo.lock_agent(agent_id)
        agent = self._key(agent, key_hash)
        await self._verified(user_id)
        await self._assign(agent, user_id, key_hash)
        return {"agent_id": str(agent_id), "owner_user_id": str(user_id), "status": "linked"}


def get_agent_ownership_service(db: AsyncSession = Depends(get_db)) -> AgentOwnershipService:
    """Bind ownership policy to the same request-owned transaction as authentication."""
    return AgentOwnershipService(db)

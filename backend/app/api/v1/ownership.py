"""
Ownership API — wallet, $ASPORE and contributor-share endpoints
===============================================================
Endpoints:
  PATCH  /api/v1/users/wallet         — connect wallet (EIP-191 sig verify)
  PATCH  /api/v1/users/solana-wallet   — connect Solana wallet for $ASPORE payouts
  DELETE /api/v1/users/solana-wallet   — disconnect Solana wallet
  GET    /api/v1/users/me/payouts      — payout history
  POST   /api/v1/agents/link-owner     — link agent to human user (JWT + X-API-Key)
  GET    /api/v1/projects/{id}/ownership — per-project contributor shares
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.deps import CurrentUser, DatabaseSession
from app.repositories import ownership_repo
from app.schemas.ownership import (
    AgentOwnerClaimCreate,
    AgentOwnerClaimDecision,
    AgentOwnerClaimResponse,
    AsporeDepositRequest,
    ContributorShare,
    LinkOwnerRequest,
    ProjectOwnershipResponse,
    SolanaWalletConnectRequest,
    WalletConnectRequest,
)
from app.services.agent_ownership_service import AgentOwnershipService, get_agent_ownership_service
from app.services.agent_service import get_agent_by_api_key
from app.services.payout_service import PayoutService, get_payout_service

router = APIRouter(tags=["ownership"])


# ──────────────────────────────────────────────────────────────────────────────
# PATCH /users/wallet — connect wallet
# ──────────────────────────────────────────────────────────────────────────────


@router.patch("/users/wallet")
async def connect_wallet(
    body: WalletConnectRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
):
    """
    Connect an Ethereum wallet to the user account via EIP-191 signature.
    The frontend must sign `message` with the wallet — we recover the signer
    and verify it matches the supplied wallet_address.
    """
    try:
        signable = encode_defunct(text=body.message)
        recovered = Account.recover_message(signable, signature=body.signature)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid signature: {exc}") from exc

    if recovered.lower() != body.wallet_address.lower():
        raise HTTPException(
            status_code=400,
            detail="Signature signer does not match wallet_address",
        )

    if await ownership_repo.check_wallet_uniqueness(db, body.wallet_address, str(current_user.id)):
        raise HTTPException(status_code=409, detail="Wallet already connected to another account")

    await ownership_repo.update_user_wallet(db, str(current_user.id), body.wallet_address)
    await db.commit()
    return {"wallet_address": body.wallet_address.lower(), "status": "connected"}


# ──────────────────────────────────────────────────────────────────────────────
# PATCH /users/solana-wallet — connect Solana wallet for $ASPORE payouts
# ──────────────────────────────────────────────────────────────────────────────


@router.patch("/users/solana-wallet")
async def connect_solana_wallet(
    body: SolanaWalletConnectRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
    svc: PayoutService = Depends(get_payout_service),
):
    """
    Connect a Solana wallet to the user account for $ASPORE token payouts.
    No signature verification — just saves the address (user is already authenticated via JWT).
    """
    try:
        await svc.connect_solana_wallet(str(current_user.id), body.solana_wallet)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    await db.commit()
    return {"solana_wallet": body.solana_wallet, "status": "connected"}


@router.delete("/users/solana-wallet")
async def disconnect_solana_wallet(
    current_user: CurrentUser,
    db: DatabaseSession,
    svc: PayoutService = Depends(get_payout_service),
):
    """Disconnect Solana wallet from user account."""
    await svc.disconnect_solana_wallet(str(current_user.id))
    await db.commit()
    return {"status": "disconnected"}


@router.get("/users/me/payouts")
async def get_my_payouts(
    current_user: CurrentUser,
    svc: PayoutService = Depends(get_payout_service),
):
    """Get current user's $ASPORE payout history."""
    return await svc.get_user_payouts(str(current_user.id))


# ──────────────────────────────────────────────────────────────────────────────
# $ASPORE Balance & Deposits
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/users/me/aspore")
async def get_aspore_balance(
    current_user: CurrentUser,
    svc: PayoutService = Depends(get_payout_service),
):
    """Get current user's $ASPORE deposit balance."""
    balance = await svc.get_balance(str(current_user.id))
    return {"aspore_balance": balance}


@router.post("/users/me/aspore/deposit")
async def deposit_aspore(
    body: AsporeDepositRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
    svc: PayoutService = Depends(get_payout_service),
):
    """Verify a user-bound wallet claim and one finalized direct SPL transfer."""
    try:
        result = await svc.verify_and_credit_deposit(str(current_user.id), body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return {
        "status": "deposited",
        "amount": result["amount"],
        "balance_after": result["balance_after"],
        "tx_signature": body.tx_signature,
    }


@router.get("/users/me/aspore/transactions")
async def get_aspore_transactions(
    current_user: CurrentUser,
    svc: PayoutService = Depends(get_payout_service),
):
    """Get current user's $ASPORE transaction history."""
    return await svc.get_transactions(str(current_user.id))


# ──────────────────────────────────────────────────────────────────────────────
# POST /agents/link-owner — link agent to human user
# ──────────────────────────────────────────────────────────────────────────────


@router.post("/agents/link-owner")
async def link_agent_owner(
    body: LinkOwnerRequest,
    current_user: CurrentUser,
    db: DatabaseSession,
    x_api_key: str = Header(..., alias="X-API-Key"),
    svc: AgentOwnershipService = Depends(get_agent_ownership_service),
):
    """Link with both proofs while preserving existing owners and historical attribution."""
    result = await svc.legacy(
        body.agent_id, current_user.id, hashlib.sha256(x_api_key.encode()).hexdigest()
    )
    await db.commit()
    return result


# ──────────────────────────────────────────────────────────────────────────────
# GET /projects/{id}/ownership
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/ownership", response_model=ProjectOwnershipResponse)
async def get_project_ownership(
    project_id: str,
    db: DatabaseSession,
):
    """Public endpoint — returns contributor shares."""
    project_title = await ownership_repo.get_project_title(db, project_id)
    if not project_title:
        raise HTTPException(status_code=404, detail="Project not found")

    contrib_rows = await ownership_repo.get_contributor_shares(db, project_id)

    contributors: list[ContributorShare] = []
    for row in contrib_rows:
        contributors.append(
            ContributorShare(
                agent_id=str(row.agent_id),
                agent_name=row.agent_name,
                owner_user_id=str(row.owner_user_id) if row.owner_user_id else None,
                owner_name=row.owner_name,
                wallet_address=row.wallet_address,
                contribution_points=row.contribution_points,
                share_pct=float(row.share_pct),
            )
        )

    return ProjectOwnershipResponse(
        project_id=project_id,
        project_title=project_title,
        contributors=contributors,
    )


@router.post("/users/me/agent-claims", response_model=AgentOwnerClaimResponse)
async def create_owner_claim(
    body: AgentOwnerClaimCreate,
    current_user: CurrentUser,
    db: DatabaseSession,
    svc: AgentOwnershipService = Depends(get_agent_ownership_service),
):
    """Create a verified human's non-authorizing intent for an exact agent."""
    result = await svc.create(body.agent_id, current_user.id)
    await db.commit()
    return result


@router.get("/users/me/agent-claims/{claim_id}", response_model=AgentOwnerClaimResponse)
async def get_owner_claim(
    claim_id: UUID,
    current_user: CurrentUser,
    svc: AgentOwnershipService = Depends(get_agent_ownership_service),
):
    """Return only the authenticated human's intent."""
    return await svc.human(claim_id, current_user.id)


@router.delete("/users/me/agent-claims/{claim_id}", response_model=AgentOwnerClaimResponse)
async def cancel_owner_claim(
    claim_id: UUID,
    current_user: CurrentUser,
    db: DatabaseSession,
    svc: AgentOwnershipService = Depends(get_agent_ownership_service),
):
    """Withdraw human consent while serializing against agent approval."""
    result = await svc.human(claim_id, current_user.id, cancel=True)
    await db.commit()
    return result


@router.get("/agents/owner-claims/{claim_id}", response_model=AgentOwnerClaimResponse)
async def get_agent_owner_claim(
    claim_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentOwnershipService = Depends(get_agent_ownership_service),
):
    """Show the exact human subject only to the intended active agent."""
    return await svc.agent(claim_id, agent)


@router.post("/agents/owner-claims/{claim_id}", response_model=AgentOwnerClaimResponse)
async def decide_owner_claim(
    claim_id: UUID,
    body: AgentOwnerClaimDecision,
    db: DatabaseSession,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentOwnershipService = Depends(get_agent_ownership_service),
):
    """Approve or deny one exact intent using current independent agent control."""
    result = await svc.decide(claim_id, agent, body)
    await db.commit()
    return result

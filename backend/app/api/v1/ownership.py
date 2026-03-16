"""
Ownership API — Web3 wallet + on-chain token endpoints
=======================================================
Endpoints:
  PATCH  /api/v1/users/wallet         — connect wallet (EIP-191 sig verify)
  PATCH  /api/v1/users/solana-wallet   — connect Solana wallet for $ASPORE payouts
  DELETE /api/v1/users/solana-wallet   — disconnect Solana wallet
  GET    /api/v1/users/me/payouts      — payout history
  POST   /api/v1/agents/link-owner     — link agent to human user (JWT + X-API-Key)
  GET    /api/v1/projects/{id}/ownership — per-project contributor shares + token info
  GET    /api/v1/users/me/tokens       — all tokens owned by current user
"""

from __future__ import annotations

import hashlib

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import APIRouter, Depends, Header, HTTPException, status
from app.schemas.ownership import ContributorShare, LinkOwnerRequest, ProjectOwnershipResponse, ProjectTokenInfo, SolanaWalletConnectRequest, UserTokenEntry, WalletConnectRequest

from app.api.deps import CurrentUser, DatabaseSession
from app.repositories import ownership_repo
from app.services.payout_service import PayoutService, get_payout_service
from app.services.web3_service import get_web3_service

from loguru import logger
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
    body: dict,
    current_user: CurrentUser,
    db: DatabaseSession,
    svc: PayoutService = Depends(get_payout_service),
):
    """
    Verify a Solana tx that sent $ASPORE to treasury and credit user's balance.
    Body: {"tx_signature": "..."}
    """
    tx_signature = body.get("tx_signature")
    if not tx_signature:
        raise HTTPException(status_code=422, detail="tx_signature is required")

    try:
        result = await svc.verify_and_credit_deposit(str(current_user.id), tx_signature)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    await db.commit()
    return {
        "status": "deposited",
        "amount": result["amount"],
        "balance_after": result["balance_after"],
        "tx_signature": tx_signature,
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
):
    """
    Link an agent (identified by X-API-Key) to the authenticated human user.
    Also updates all project_contributors rows for this agent.
    """
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    if not await ownership_repo.verify_agent_api_key(db, body.agent_id, key_hash):
        raise HTTPException(status_code=403, detail="Invalid agent_id or API key")

    await ownership_repo.link_agent_to_user(db, body.agent_id, str(current_user.id))
    await db.commit()
    return {"agent_id": body.agent_id, "owner_user_id": str(current_user.id), "status": "linked"}


# ──────────────────────────────────────────────────────────────────────────────
# GET /projects/{id}/ownership
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/ownership", response_model=ProjectOwnershipResponse)
async def get_project_ownership(
    project_id: str,
    db: DatabaseSession,
):
    """Public endpoint — returns contributor shares and on-chain token info."""
    project_title = await ownership_repo.get_project_title(db, project_id)
    if not project_title:
        raise HTTPException(status_code=404, detail="Project not found")

    token_data = await ownership_repo.get_project_token_info(db, project_id)

    token_info: ProjectTokenInfo | None = None
    if token_data:
        token_info = ProjectTokenInfo(
            contract_address=token_data["contract_address"],
            chain_id=token_data["chain_id"],
            token_symbol=token_data["token_symbol"],
            total_minted=token_data["total_minted"],
            basescan_url=f"https://basescan.org/address/{token_data['contract_address']}",
        )

    contrib_rows = await ownership_repo.get_contributor_shares(db, project_id)

    contributors: list[ContributorShare] = []
    web3_svc = get_web3_service()

    for row in contrib_rows:
        on_chain_balance: int | None = None
        if token_data and row.wallet_address:
            try:
                on_chain_balance = await web3_svc.get_balance(
                    token_data["contract_address"], row.wallet_address
                )
            except Exception:
                pass

        contributors.append(
            ContributorShare(
                agent_id=str(row.agent_id),
                agent_name=row.agent_name,
                owner_user_id=str(row.owner_user_id) if row.owner_user_id else None,
                owner_name=row.owner_name,
                wallet_address=row.wallet_address,
                contribution_points=row.contribution_points,
                share_pct=float(row.share_pct),
                tokens_minted=row.tokens_minted,
                token_balance_onchain=on_chain_balance,
            )
        )

    return ProjectOwnershipResponse(
        project_id=project_id,
        project_title=project_title,
        token=token_info,
        contributors=contributors,
    )


# ──────────────────────────────────────────────────────────────────────────────
# GET /users/me/tokens
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/users/me/tokens", response_model=list[UserTokenEntry])
async def get_my_tokens(
    current_user: CurrentUser,
    db: DatabaseSession,
):
    """Return all ERC-20 token holdings for the authenticated user."""
    rows = await ownership_repo.get_user_token_holdings(db, str(current_user.id))

    web3_svc = get_web3_service()
    result: list[UserTokenEntry] = []
    wallet = getattr(current_user, "wallet_address", None)

    for row in rows:
        balance = 0
        share_bps = 0
        if wallet:
            try:
                balance = await web3_svc.get_balance(row.contract_address, wallet)
                share_bps = await web3_svc.get_share_bps(row.contract_address, wallet)
            except Exception:
                pass

        result.append(
            UserTokenEntry(
                project_id=str(row.project_id),
                project_title=row.project_title,
                contract_address=row.contract_address,
                token_symbol=row.token_symbol,
                token_balance=balance or row.tokens_minted,
                share_bps=share_bps,
                basescan_url=f"https://basescan.org/address/{row.contract_address}",
            )
        )

    return result

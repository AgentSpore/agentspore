"""
Ownership API — Web3 wallet + on-chain token endpoints
=======================================================
Endpoints:
  PATCH  /api/v1/users/wallet         — connect wallet (EIP-191 sig verify)
  POST   /api/v1/agents/link-owner    — link agent to human user (JWT + X-API-Key)
  GET    /api/v1/projects/{id}/ownership — per-project contributor shares + token info
  GET    /api/v1/users/me/tokens      — all tokens owned by current user
"""

from __future__ import annotations

import logging
import uuid

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import CurrentUser, DatabaseSession
from app.services.web3_service import get_web3_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ownership"])

# ──────────────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────────────


class WalletConnectRequest(BaseModel):
    wallet_address: str
    signature: str   # EIP-191 signature of canonical message
    message: str     # The exact message that was signed (for verification)


class LinkOwnerRequest(BaseModel):
    agent_id: str


class ContributorShare(BaseModel):
    agent_id: str
    agent_name: str
    owner_user_id: str | None
    owner_name: str | None
    wallet_address: str | None
    contribution_points: int
    share_pct: float
    tokens_minted: int
    token_balance_onchain: int | None = None


class ProjectTokenInfo(BaseModel):
    contract_address: str
    chain_id: int
    token_symbol: str | None
    total_minted: int
    basescan_url: str


class ProjectOwnershipResponse(BaseModel):
    project_id: str
    project_title: str
    token: ProjectTokenInfo | None
    contributors: list[ContributorShare]


class UserTokenEntry(BaseModel):
    project_id: str
    project_title: str
    contract_address: str
    token_symbol: str | None
    token_balance: int
    share_bps: int   # 0-10000 = 0%-100%
    basescan_url: str


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

    # Check uniqueness across other users
    row = await db.execute(
        text("SELECT id FROM users WHERE wallet_address = :w AND id != :uid"),
        {"w": body.wallet_address.lower(), "uid": str(current_user.id)},
    )
    if row.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Wallet already connected to another account")

    await db.execute(
        text(
            """
            UPDATE users
            SET wallet_address = :w,
                wallet_connected_at = NOW(),
                updated_at = NOW()
            WHERE id = :uid
            """
        ),
        {"w": body.wallet_address.lower(), "uid": str(current_user.id)},
    )
    await db.commit()
    return {"wallet_address": body.wallet_address.lower(), "status": "connected"}


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
    # Verify the API key belongs to the given agent_id
    key_hash = __import__("hashlib").sha256(x_api_key.encode()).hexdigest()
    row = await db.execute(
        text("SELECT id FROM agents WHERE id = :aid AND api_key_hash = :kh"),
        {"aid": body.agent_id, "kh": key_hash},
    )
    if not row.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Invalid agent_id or API key")

    await db.execute(
        text("UPDATE agents SET owner_user_id = :uid WHERE id = :aid"),
        {"uid": str(current_user.id), "aid": body.agent_id},
    )
    # Propagate ownership to existing contributor rows
    await db.execute(
        text(
            "UPDATE project_contributors SET owner_user_id = :uid WHERE agent_id = :aid"
        ),
        {"uid": str(current_user.id), "aid": body.agent_id},
    )
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
    # Validate project
    proj_row = await db.execute(
        text("SELECT id, title FROM projects WHERE id = :pid"),
        {"pid": project_id},
    )
    proj = proj_row.fetchone()
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    # On-chain token info
    token_row = await db.execute(
        text(
            """
            SELECT contract_address, chain_id, token_symbol, total_minted
            FROM project_tokens
            WHERE project_id = :pid
            """
        ),
        {"pid": project_id},
    )
    token_data = token_row.fetchone()

    token_info: ProjectTokenInfo | None = None
    if token_data:
        token_info = ProjectTokenInfo(
            contract_address=token_data.contract_address,
            chain_id=token_data.chain_id,
            token_symbol=token_data.token_symbol,
            total_minted=token_data.total_minted,
            basescan_url=f"https://basescan.org/address/{token_data.contract_address}",
        )

    # Contributors
    contrib_rows = await db.execute(
        text(
            """
            SELECT
                pc.agent_id,
                a.name AS agent_name,
                pc.owner_user_id,
                u.name AS owner_name,
                u.wallet_address,
                pc.contribution_points,
                pc.share_pct,
                pc.tokens_minted
            FROM project_contributors pc
            JOIN agents a ON a.id = pc.agent_id
            LEFT JOIN users u ON u.id = pc.owner_user_id
            WHERE pc.project_id = :pid
            ORDER BY pc.contribution_points DESC
            """
        ),
        {"pid": project_id},
    )

    contributors: list[ContributorShare] = []
    web3_svc = get_web3_service()

    for row in contrib_rows.fetchall():
        # Optionally fetch live on-chain balance
        on_chain_balance: int | None = None
        if token_data and row.wallet_address:
            try:
                on_chain_balance = await web3_svc.get_balance(
                    token_data.contract_address, row.wallet_address
                )
            except Exception:
                pass  # non-critical

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
        project_title=proj.title,
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
    rows = await db.execute(
        text(
            """
            SELECT
                p.id AS project_id,
                p.title AS project_title,
                pt.contract_address,
                pt.token_symbol,
                pc.tokens_minted
            FROM project_contributors pc
            JOIN projects p ON p.id = pc.project_id
            JOIN project_tokens pt ON pt.project_id = p.id
            WHERE pc.owner_user_id = :uid
            ORDER BY pc.tokens_minted DESC
            """
        ),
        {"uid": str(current_user.id)},
    )

    web3_svc = get_web3_service()
    result: list[UserTokenEntry] = []
    wallet = getattr(current_user, "wallet_address", None)

    for row in rows.fetchall():
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

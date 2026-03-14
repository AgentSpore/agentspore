"""Ownership schemas."""

import re

from pydantic import BaseModel, field_validator


class WalletConnectRequest(BaseModel):
    wallet_address: str
    signature: str
    message: str


class SolanaWalletConnectRequest(BaseModel):
    solana_wallet: str

    @field_validator("solana_wallet")
    @classmethod
    def validate_solana_address(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", v):
            raise ValueError("Invalid Solana address (base58, 32-44 chars)")
        return v


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
    share_bps: int
    basescan_url: str

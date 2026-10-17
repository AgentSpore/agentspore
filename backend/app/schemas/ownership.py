"""Ownership schemas."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from solders.pubkey import Pubkey
from solders.signature import Signature


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


class AsporeDepositRequest(BaseModel):
    """A wallet-signed claim bound to one authenticated user and transaction."""

    model_config = ConfigDict(extra="forbid", strict=True)
    tx_signature: str = Field(max_length=88)
    solana_wallet: str = Field(max_length=44)
    claim_signature: str = Field(max_length=88)

    @field_validator("tx_signature", "claim_signature")
    @classmethod
    def validate_signature(cls, value: str) -> str:
        """Require a base58-encoded 64-byte Ed25519 signature."""
        Signature.from_string(value)
        return value

    @field_validator("solana_wallet")
    @classmethod
    def validate_wallet(cls, value: str) -> str:
        """Require a base58-encoded 32-byte public key."""
        Pubkey.from_string(value)
        return value


class DepositTokenAmount(BaseModel):
    """Exact raw token quantity; display floats are deliberately ignored."""

    model_config = ConfigDict(strict=True)
    amount: str = Field(pattern=r"^(0|[1-9][0-9]*)$", max_length=25)
    decimals: int


class DepositTokenBalance(BaseModel):
    """One RPC token-account balance at an account-key index."""

    model_config = ConfigDict(strict=True)
    account_index: int = Field(ge=0, alias="accountIndex")
    mint: str
    owner: str
    ui_token_amount: DepositTokenAmount = Field(alias="uiTokenAmount")


class DepositAccountKey(BaseModel):
    """Parsed transaction account with an explicit signer flag."""

    model_config = ConfigDict(strict=True)
    pubkey: str
    signer: bool


class DepositTransferInfo(BaseModel):
    """Supported direct SPL transfer fields; multisig fields are not accepted."""

    model_config = ConfigDict(strict=True, extra="forbid")
    source: str
    destination: str
    authority: str
    amount: str | None = Field(default=None, pattern=r"^(0|[1-9][0-9]*)$", max_length=25)
    mint: str | None = None
    token_amount: DepositTokenAmount | None = Field(default=None, alias="tokenAmount")


class DepositParsedTransfer(BaseModel):
    """The two supported parsed SPL transfer variants."""

    model_config = ConfigDict(strict=True, extra="forbid")
    type: Literal["transfer", "transferChecked"]
    info: DepositTransferInfo


class DepositInstruction(BaseModel):
    """An SPL transfer or a non-token-moving compute-budget instruction."""

    model_config = ConfigDict(strict=True)
    program_id: str = Field(alias="programId")
    parsed: DepositParsedTransfer | None = None
    accounts: list[str] | None = None
    data: str | None = None


class DepositMessage(BaseModel):
    """Account membership and top-level instructions from jsonParsed RPC."""

    model_config = ConfigDict(strict=True)
    account_keys: list[DepositAccountKey] = Field(alias="accountKeys")
    instructions: list[DepositInstruction]


class DepositTransaction(BaseModel):
    """Transaction identity and message returned by the trusted RPC."""

    model_config = ConfigDict(strict=True)
    signatures: list[str]
    message: DepositMessage


class DepositMeta(BaseModel):
    """Require an explicit successful result and recorded balance metadata."""

    model_config = ConfigDict(strict=True)
    err: None
    pre_token_balances: list[DepositTokenBalance] = Field(alias="preTokenBalances")
    post_token_balances: list[DepositTokenBalance] = Field(alias="postTokenBalances")
    inner_instructions: list[dict[str, object]] | None = Field(alias="innerInstructions")


class DepositTransactionResult(BaseModel):
    """Finalized transaction data needed to attribute an exact transfer."""

    model_config = ConfigDict(strict=True)
    transaction: DepositTransaction
    meta: DepositMeta


class DepositRpcResponse(BaseModel):
    """A successful JSON-RPC response, excluding ambiguous error envelopes."""

    model_config = ConfigDict(strict=True, extra="forbid")
    jsonrpc: Literal["2.0"]
    id: int
    result: DepositTransactionResult

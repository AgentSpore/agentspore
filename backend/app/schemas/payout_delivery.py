"""Strict input and balance evidence for payout receipt verification."""

import hashlib
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)
from solders.hash import Hash, ParseHashError
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.token.associated import get_associated_token_address


def public_key(value: str) -> str:
    """Validate a canonical public key."""
    Pubkey.from_string(value)
    return value


def solana_hash(value: str) -> str:
    """Validate a Solana hash while normalizing the native parser exception."""
    try:
        return str(Hash.from_string(value))
    except ParseHashError:
        raise ValueError from None


ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SYSTEM_PROGRAM = "11111111111111111111111111111111"

PublicKey = Annotated[str, AfterValidator(public_key)]


def raw_units(value: str) -> str:
    """Require canonical decimal units within the unsigned 64-bit range."""
    if int(value) > 2**64 - 1:
        raise ValueError
    return value


RawUnits = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,19})$"), AfterValidator(raw_units)]


class TransferIntent(BaseModel):
    """Require every immutable identity without transport defaults."""

    model_config = ConfigDict(strict=True, extra="forbid")
    genesis_hash: Annotated[str, AfterValidator(solana_hash)]
    mint: PublicKey
    program: Literal["TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"]
    source: PublicKey
    destination: PublicKey
    authority: PublicKey
    fee_payer: PublicKey
    recipient: PublicKey
    decimals: Annotated[int, Field(ge=0, le=255)]
    raw_amount: Annotated[RawUnits, Field(pattern=r"^[1-9][0-9]*$")]

    @model_validator(mode="after")
    def distinct_token_accounts(self) -> Self:
        """Reject overlapping token roles before interpreting receipt evidence."""
        if len({self.source, self.destination, self.mint, self.program}) != 4:
            raise ValueError
        return self

    @model_validator(mode="after")
    def destination_is_recipient_ata(self) -> Self:
        """Pin the destination to the recipient's Token-2022 ATA and keep the wallet distinct."""
        reserved = {
            self.authority,
            self.fee_payer,
            self.source,
            self.mint,
            self.program,
            ATA_PROGRAM,
            SYSTEM_PROGRAM,
        }
        if self.recipient in reserved or self.destination != self.associated_destination():
            raise ValueError
        return self

    def associated_destination(self) -> str:
        """Derive the Token-2022 associated token account of the recipient wallet."""
        return str(
            get_associated_token_address(
                Pubkey.from_string(self.recipient),
                Pubkey.from_string(self.mint),
                Pubkey.from_string(self.program),
            )
        )


class ReceiptIntent(TransferIntent):
    """Require the original receipt signature and explicit RPC endpoint."""

    signature: Annotated[str, AfterValidator(lambda value: str(Signature.from_string(value)))]
    rpc_endpoint: AnyHttpUrl


Namespace = Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")]
AllocationId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]


class AllocationKey(BaseModel):
    """Identify a producer's immutable allocation without disclosing its payload."""

    model_config = ConfigDict(strict=True, extra="forbid")
    producer_namespace: Namespace
    allocation_id: AllocationId


class AllocationIntent(TransferIntent, AllocationKey):
    """Require a technical allocation identity and complete transfer payload."""


class TokenAmount(BaseModel):
    """Validate raw receipt units without interpreting UI amounts."""

    model_config = ConfigDict(strict=True)
    amount: RawUnits
    decimals: Annotated[int, Field(ge=0, le=255)]


class TokenBalance(BaseModel):
    """Require historical account identity and token program ownership."""

    model_config = ConfigDict(strict=True)
    account_index: Annotated[int, Field(ge=0, alias="accountIndex")]
    mint: PublicKey
    owner: PublicKey
    program_id: PublicKey = Field(alias="programId")
    token_amount: TokenAmount = Field(alias="uiTokenAmount")


class ClaimKey(BaseModel):
    """Identify one admitted intent for lease coordination."""

    model_config = ConfigDict(strict=True, extra="forbid")
    intent_id: UUID


class ClaimRequest(ClaimKey):
    """Require an explicit coordinator and bounded operational TTL."""

    worker_id: UUID
    ttl_seconds: Annotated[int, Field(ge=1, le=300)]


class ClaimFence(ClaimKey):
    """Identify one exact persisted ownership generation."""

    worker_id: UUID
    epoch: Annotated[int, Field(ge=1, le=9223372036854775807)]


class RenewRequest(ClaimFence):
    """Renew one live generation without changing its identity."""

    ttl_seconds: Annotated[int, Field(ge=1, le=300)]


def format_one(value: object) -> int:
    """Reject bool and every format version except the integer one."""
    if type(value) is not int or value != 1:
        raise ValueError
    return value


class PreparationContext(ClaimFence):
    """Require explicit asserted network and lifetime without live-provider defaults."""

    genesis_hash: Annotated[str, AfterValidator(solana_hash)]
    last_valid_block_height: RawUnits


class CompileRequest(PreparationContext):
    """Bind one unsigned message to an explicit blockhash and live fence."""

    recent_blockhash: Annotated[str, AfterValidator(solana_hash)]


class SigningRequest(CompileRequest):
    """Carry account-bearing unsigned bytes only through the private artifact channel."""

    format_version: Annotated[Literal[1], BeforeValidator(format_one)] = 1
    unsigned_message_base64: str
    message_digest: str


class SignedEnvelope(PreparationContext):
    """Accept only the versioned signed handoff fields, never a claimed signature."""

    format_version: Annotated[Literal[1], BeforeValidator(format_one)]
    signed_transaction_base64: Annotated[str, Field(min_length=4, max_length=1644, repr=False)]


class SignedCandidate(BaseModel):
    """Hold native-verified material internally without ordinary CLI serialization."""

    model_config = ConfigDict(strict=True, frozen=True)
    signed_bytes: bytes = Field(repr=False)
    first_signature: bytes = Field(repr=False)
    recent_blockhash: str
    message_bytes: bytes = Field(repr=False)
    transaction_digest: str


def plain_endpoint(value: AnyHttpUrl) -> AnyHttpUrl:
    """Keep credentials and query tokens out of operator JSON; plain http only on loopback."""
    if value.username or value.password or value.query or value.fragment:
        raise ValueError
    if value.scheme != "https" and value.host not in {"localhost", "127.0.0.1", "[::1]"}:
        raise ValueError
    return value


class NetworkRequest(ClaimKey):
    """Name only the persisted attempt, the trusted network and the RPC to ask."""

    trusted_genesis_hash: Annotated[str, AfterValidator(solana_hash)]
    rpc_endpoint: Annotated[AnyHttpUrl, AfterValidator(plain_endpoint)]


class PendingRequest(BaseModel):
    """Bound the identifier listing; it carries no filter on amounts or wallets."""

    model_config = ConfigDict(strict=True, extra="forbid")
    limit: Annotated[int, Field(ge=1, le=1000)]


class NetworkConfig(BaseModel):
    """Server-side network identity; read from a private file, never from caller input."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    rpc_endpoint: Annotated[AnyHttpUrl, AfterValidator(plain_endpoint)]
    trusted_genesis_hash: Annotated[str, AfterValidator(solana_hash)]
    signer_pubkey: PublicKey
    source_token_account: PublicKey
    create_recipient_ata: bool = False


class EmptyRequest(BaseModel):
    """Stdin for commands that take no caller data."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ReconcileRequest(NetworkRequest):
    """Confirm the persisted attempt from a finalized receipt."""


class SendRequest(NetworkRequest):
    """Broadcast the exact persisted signed transaction."""


class ConfirmationEvidence(BaseModel):
    """Hold the minimal verified fact; no bytes, signature, account or RPC body."""

    model_config = ConfigDict(strict=True, frozen=True)
    finalized_slot: RawUnits
    verifier_version: Annotated[int, Field(ge=1)]
    evidence_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ReconcileTarget(BaseModel):
    """Persisted attempt material and any stored confirmation, read from the database only."""

    model_config = ConfigDict(frozen=True)
    transfer: TransferIntent
    signed_bytes: bytes = Field(repr=False)
    first_signature: bytes = Field(repr=False)
    stored: ConfirmationEvidence | None

    @property
    def transaction_digest(self) -> str:
        """Identify the exact stored wire without exposing it."""
        return hashlib.sha256(self.signed_bytes).hexdigest()

    @classmethod
    def from_row(cls, row: dict) -> Self:
        """Build from a joined attempt/intent/confirmation row."""
        stored = None
        if row["evidence_digest"] is not None:
            stored = ConfirmationEvidence.model_validate(
                {name: row[name] for name in ConfirmationEvidence.model_fields}
            )
        return cls(
            transfer=TransferIntent.model_validate(
                {name: row[name] for name in TransferIntent.model_fields}
            ),
            signed_bytes=bytes(row["signed_bytes"]),
            first_signature=bytes(row["first_signature"]),
            stored=stored,
        )

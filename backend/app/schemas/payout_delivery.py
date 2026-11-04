"""Strict input and balance evidence for payout receipt verification."""

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator
from solders.hash import Hash, ParseHashError
from solders.pubkey import Pubkey
from solders.signature import Signature


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


PublicKey = Annotated[str, AfterValidator(public_key)]


def raw_units(value: str) -> str:
    """Require canonical decimal units within the unsigned 64-bit range."""
    if int(value) > 2**64 - 1:
        raise ValueError
    return value


RawUnits = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,19})$"), AfterValidator(raw_units)]


class ReceiptIntent(BaseModel):
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
    signature: Annotated[str, AfterValidator(lambda value: str(Signature.from_string(value)))]
    rpc_endpoint: AnyHttpUrl

    @model_validator(mode="after")
    def distinct_token_accounts(self) -> Self:
        """Reject overlapping token roles before interpreting receipt evidence."""
        if len({self.source, self.destination, self.mint, self.program}) != 4:
            raise ValueError
        return self


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

"""Compile and validate the deliberately offline legacy Token-2022 transfer subset."""

import base64
import hashlib

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import SanitizeError, TransactionError, VersionedTransaction
from spl.token.constants import TOKEN_2022_PROGRAM_ID
from spl.token.instructions import TransferCheckedParams, transfer_checked

from app.schemas.payout_delivery import (
    ATA_PROGRAM,
    SYSTEM_PROGRAM,
    CompileRequest,
    SignedCandidate,
    SigningRequest,
    TransferIntent,
)


class InvalidPayoutCandidateError(ValueError):
    """Reject untrusted offline material without exposing native exception details."""


def build_payout_message(
    intent: TransferIntent, blockhash: str, create_ata: bool = False
) -> Message:
    """Use native SPL compilation; with `create_ata` the transfer follows CreateIdempotent."""
    if {intent.authority, intent.fee_payer} & {
        intent.mint,
        intent.program,
        intent.source,
        intent.destination,
    }:
        raise InvalidPayoutCandidateError
    instructions = [
        transfer_checked(
            TransferCheckedParams(
                TOKEN_2022_PROGRAM_ID,
                Pubkey.from_string(intent.source),
                Pubkey.from_string(intent.mint),
                Pubkey.from_string(intent.destination),
                Pubkey.from_string(intent.authority),
                int(intent.raw_amount),
                intent.decimals,
            )
        )
    ]
    if create_ata:
        if intent.destination != intent.associated_destination():
            raise InvalidPayoutCandidateError
        instructions.insert(0, _create_ata_instruction(intent))
    return Message.new_with_blockhash(
        instructions, Pubkey.from_string(intent.fee_payer), Hash.from_string(blockhash)
    )


def _create_ata_instruction(intent: TransferIntent) -> Instruction:
    """CreateIdempotent: the fee payer funds the recipient's account, owner is the wallet."""
    metas = [
        AccountMeta(Pubkey.from_string(intent.fee_payer), True, True),
        AccountMeta(Pubkey.from_string(intent.destination), False, True),
        AccountMeta(Pubkey.from_string(intent.recipient), False, False),
        AccountMeta(Pubkey.from_string(intent.mint), False, False),
        AccountMeta(Pubkey.from_string(SYSTEM_PROGRAM), False, False),
        AccountMeta(TOKEN_2022_PROGRAM_ID, False, False),
    ]
    return Instruction(Pubkey.from_string(ATA_PROGRAM), b"\x01", metas)


def compile_signing_request(
    intent: TransferIntent, request: CompileRequest, create_ata: bool = False
) -> SigningRequest:
    """Produce exact message bytes without signing or asserting network truth."""
    if request.genesis_hash != intent.genesis_hash:
        raise InvalidPayoutCandidateError
    raw = bytes(build_payout_message(intent, request.recent_blockhash, create_ata))
    return SigningRequest(
        **request.model_dump(),
        unsigned_message_base64=base64.b64encode(raw).decode(),
        message_digest=hashlib.sha256(raw).hexdigest(),
    )


def parse_signed_candidate(encoded: str) -> SignedCandidate:
    """Bound, sanitize and verify every signature on a canonical native legacy wire."""
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not 1 <= len(raw) <= 1232 or base64.b64encode(raw).decode() != encoded:
            raise InvalidPayoutCandidateError
        transaction = VersionedTransaction.from_bytes(raw).into_legacy_transaction()
        if transaction is None or bytes(transaction) != raw:
            raise InvalidPayoutCandidateError
        transaction.sanitize()
        transaction.verify()
        return SignedCandidate(
            signed_bytes=raw,
            first_signature=bytes(transaction.signatures[0]),
            recent_blockhash=str(transaction.message.recent_blockhash),
            message_bytes=bytes(transaction.message),
            transaction_digest=hashlib.sha256(raw).hexdigest(),
        )
    except (ValueError, TypeError, IndexError, SanitizeError, TransactionError):
        # Native parsing errors must not escape the privileged material boundary.
        raise InvalidPayoutCandidateError from None


def bind_signed_candidate(intent: TransferIntent, candidate: SignedCandidate, genesis: str) -> None:
    """Require exact expected bytes; the stored instruction count picks shape A (1) or B (2)."""
    try:
        with_create = len(Message.from_bytes(candidate.message_bytes).instructions) == 2
    except (ValueError, TypeError):
        raise InvalidPayoutCandidateError from None
    if (
        genesis != intent.genesis_hash
        or bytes(build_payout_message(intent, candidate.recent_blockhash, with_create))
        != candidate.message_bytes
    ):
        raise InvalidPayoutCandidateError

"""Shape A/B payout messages: B must equal the signer's CreateIdempotent + transfer bytes."""

import base64
import hashlib
import os
from uuid import uuid4

import pytest
from pydantic import ValidationError
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.token.associated import get_associated_token_address
from solders.transaction import Transaction

from app.schemas.payout_delivery import CompileRequest, NetworkConfig, TransferIntent
from app.services.payout_transaction import (
    InvalidPayoutCandidateError,
    bind_signed_candidate,
    build_payout_message,
    compile_signing_request,
    parse_signed_candidate,
)

TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
SYSTEM = Pubkey.default()


@pytest.fixture
def payer():
    return Keypair()


@pytest.fixture
def blockhash():
    return str(Hash.from_bytes(os.urandom(32)))


@pytest.fixture
def intent(payer):
    mint, recipient = Pubkey.new_unique(), Keypair().pubkey()
    return TransferIntent(
        genesis_hash=str(Hash.default()),
        mint=str(mint),
        program="TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
        source=str(get_associated_token_address(payer.pubkey(), mint, TOKEN_2022)),
        destination=str(get_associated_token_address(recipient, mint, TOKEN_2022)),
        authority=str(payer.pubkey()),
        fee_payer=str(payer.pubkey()),
        recipient=str(recipient),
        decimals=6,
        raw_amount="1500000",
    )


def independent_b_wire(intent: TransferIntent, blockhash: str) -> bytes:
    """Mirror of the signer's `_expected_message`: raw Instruction/AccountMeta, no SPL helpers."""
    payer, mint = Pubkey.from_string(intent.fee_payer), Pubkey.from_string(intent.mint)
    wallet = Pubkey.from_string(intent.recipient)
    ata = get_associated_token_address(wallet, mint, TOKEN_2022)
    create = Instruction(
        ATA_PROGRAM,
        b"\x01",
        [
            AccountMeta(payer, True, True),
            AccountMeta(ata, False, True),
            AccountMeta(wallet, False, False),
            AccountMeta(mint, False, False),
            AccountMeta(SYSTEM, False, False),
            AccountMeta(TOKEN_2022, False, False),
        ],
    )
    data = bytes([12]) + int(intent.raw_amount).to_bytes(8, "little") + bytes([intent.decimals])
    transfer = Instruction(
        TOKEN_2022,
        data,
        [
            AccountMeta(Pubkey.from_string(intent.source), False, True),
            AccountMeta(mint, False, False),
            AccountMeta(ata, False, True),
            AccountMeta(payer, True, False),
        ],
    )
    return bytes(Message.new_with_blockhash([create, transfer], payer, Hash.from_string(blockhash)))


def request_for(blockhash: str) -> CompileRequest:
    return CompileRequest(
        intent_id=uuid4(),
        worker_id=uuid4(),
        epoch=1,
        genesis_hash=str(Hash.default()),
        recent_blockhash=blockhash,
        last_valid_block_height="100",
    )


def signed_candidate(payer: Keypair, message: Message):
    transaction = Transaction.new_unsigned(message)
    transaction.sign([payer], message.recent_blockhash)
    return parse_signed_candidate(base64.b64encode(bytes(transaction)).decode())


def test_b_bytes_equal_independent_build(intent, blockhash):
    message = build_payout_message(intent, blockhash, create_ata=True)
    first = message.instructions[0]
    assert message.account_keys[first.program_id_index] == ATA_PROGRAM
    assert first.data == b"\x01"
    assert bytes(message) == independent_b_wire(intent, blockhash)


def test_flag_off_builds_a(intent, blockhash):
    message = build_payout_message(intent, blockhash)
    assert len(message.instructions) == 1
    data = bytes([12]) + int(intent.raw_amount).to_bytes(8, "little") + bytes([intent.decimals])
    transfer = Instruction(
        TOKEN_2022,
        data,
        [
            AccountMeta(Pubkey.from_string(intent.source), False, True),
            AccountMeta(Pubkey.from_string(intent.mint), False, False),
            AccountMeta(Pubkey.from_string(intent.destination), False, True),
            AccountMeta(Pubkey.from_string(intent.authority), True, False),
        ],
    )
    payer = Pubkey.from_string(intent.fee_payer)
    expected = Message.new_with_blockhash([transfer], payer, Hash.from_string(blockhash))
    assert bytes(message) == bytes(expected)
    assert NetworkConfig.model_fields["create_recipient_ata"].default is False


def test_bind_accepts_stored_a_wire_flag_on(intent, payer, blockhash):
    stored_a = signed_candidate(payer, build_payout_message(intent, blockhash))
    stored_b = signed_candidate(payer, build_payout_message(intent, blockhash, create_ata=True))
    bind_signed_candidate(intent, stored_a, intent.genesis_hash)
    bind_signed_candidate(intent, stored_b, intent.genesis_hash)
    other = intent.model_copy(update={"raw_amount": "1"})
    with pytest.raises(InvalidPayoutCandidateError):
        bind_signed_candidate(other, stored_b, intent.genesis_hash)


def test_destination_must_be_derived_ata(intent, blockhash):
    wrong = str(Pubkey.new_unique())
    with pytest.raises(ValidationError):
        TransferIntent.model_validate({**intent.model_dump(), "destination": wrong})
    forged = intent.model_copy(update={"destination": wrong})
    with pytest.raises(InvalidPayoutCandidateError):
        build_payout_message(forged, blockhash, create_ata=True)


@pytest.mark.parametrize("role", ["authority", "fee_payer", "source", "mint", "program"])
def test_recipient_equals_authority_rejected(intent, role):
    recipient = getattr(intent, role)
    mint = Pubkey.from_string(intent.mint)
    destination = get_associated_token_address(Pubkey.from_string(recipient), mint, TOKEN_2022)
    values = {**intent.model_dump(), "recipient": recipient, "destination": str(destination)}
    with pytest.raises(ValidationError):
        TransferIntent.model_validate(values)


@pytest.mark.parametrize("program", [ATA_PROGRAM, SYSTEM])
def test_recipient_program_rejected(intent, program):
    mint = Pubkey.from_string(intent.mint)
    values = {
        **intent.model_dump(),
        "recipient": str(program),
        "destination": str(get_associated_token_address(program, mint, TOKEN_2022)),
    }
    with pytest.raises(ValidationError):
        TransferIntent.model_validate(values)


def test_digest_covers_create_ix(intent, blockhash):
    request = request_for(blockhash)
    plain = compile_signing_request(intent, request)
    with_create = compile_signing_request(intent, request, create_ata=True)
    raw = base64.b64decode(with_create.unsigned_message_base64)
    assert raw == independent_b_wire(intent, blockhash)
    assert with_create.message_digest == hashlib.sha256(raw).hexdigest()
    assert with_create.message_digest != plain.message_digest


def test_off_curve_recipient_rejected_for_b(intent, blockhash):
    pda, _ = Pubkey.find_program_address([b"vault"], ATA_PROGRAM)
    assert not pda.is_on_curve()
    mint = Pubkey.from_string(intent.mint)
    destination = str(get_associated_token_address(pda, mint, TOKEN_2022))
    off_curve = intent.model_copy(update={"recipient": str(pda), "destination": destination})
    with pytest.raises(InvalidPayoutCandidateError):
        build_payout_message(off_curve, blockhash, create_ata=True)
    assert len(build_payout_message(off_curve, blockhash).instructions) == 1

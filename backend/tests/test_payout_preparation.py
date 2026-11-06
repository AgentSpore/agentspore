"""Verify the offline native boundary using ephemeral synthetic signing only."""

import base64
import hashlib
import io
import json
import os
import struct
from uuid import uuid4

import pytest
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message, MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import Transaction, VersionedTransaction
from spl.token.constants import TOKEN_2022_PROGRAM_ID, TOKEN_PROGRAM_ID
from spl.token.instructions import TransferCheckedParams, transfer_checked

from app.cli.payout_delivery import main
from app.schemas.payout_delivery import CompileRequest, SignedEnvelope, TransferIntent
from app.services.payout_transaction import (
    InvalidPayoutCandidateError,
    bind_signed_candidate,
    build_payout_message,
    compile_signing_request,
    parse_signed_candidate,
)


class SyntheticSigner:
    """Keep ephemeral signing keys in memory and out of fixture/error representations."""

    def __init__(self, shared: bool):
        self._authority = Keypair()
        self._payer = self._authority if shared else Keypair()

    def __repr__(self):
        return "<SyntheticSigner>"

    def intent(self) -> TransferIntent:
        """Expose only synthetic public identities; no key material leaves this object."""
        return TransferIntent(
            genesis_hash=str(Hash.default()),
            mint=str(Pubkey.new_unique()),
            program="TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
            source=str(Pubkey.new_unique()),
            destination=str(Pubkey.new_unique()),
            authority=str(self._authority.pubkey()),
            fee_payer=str(self._payer.pubkey()),
            recipient=str(Pubkey.new_unique()),
            decimals=6,
            raw_amount="10",
        )

    def encode(self, message: Message, mode: str = "signed") -> str:
        """Return public synthetic wire only, never keys or a key-bearing fixture parameter."""
        keys = (
            [self._authority] if self._payer is self._authority else [self._payer, self._authority]
        )
        if mode == "v0":
            versioned = MessageV0(
                message.header,
                message.account_keys,
                message.recent_blockhash,
                message.instructions,
                [],
            )
            wire = bytes(VersionedTransaction(versioned, keys))
        else:
            transaction = Transaction.new_unsigned(message)
            if mode == "partial":
                transaction.partial_sign([self._authority], message.recent_blockhash)
            elif mode != "unsigned":
                transaction.sign(keys, message.recent_blockhash)
            if mode == "bad_signature":
                transaction.signatures = [Signature.default(), *transaction.signatures[1:]]
            elif mode == "bad_authority_signature":
                transaction.signatures = [*transaction.signatures[:-1], Signature.default()]
            wire = bytes(transaction)
        return base64.b64encode(wire).decode()


@pytest.fixture(params=[False, True], ids=["distinct-payer", "shared-payer"])
def native_case(request):
    signer = SyntheticSigner(request.param)
    return signer.intent(), signer


@pytest.fixture
def compile_request():
    return CompileRequest(
        intent_id=uuid4(),
        worker_id=uuid4(),
        epoch=1,
        genesis_hash=str(Hash.default()),
        recent_blockhash=str(Hash.from_bytes(os.urandom(32))),
        last_valid_block_height="18446744073709551615",
    )


@pytest.mark.parametrize("amount", ["1", "9223372036854775808", "18446744073709551615"])
def test_native_compile_sign_verify(native_case, compile_request, amount):
    intent, signer = native_case
    intent = intent.model_copy(update={"raw_amount": amount})
    artifact = compile_signing_request(intent, compile_request)
    raw = base64.b64decode(artifact.unsigned_message_base64)
    assert artifact.message_digest == hashlib.sha256(raw).hexdigest()
    instruction = transfer_checked(
        TransferCheckedParams(
            TOKEN_2022_PROGRAM_ID,
            Pubkey.from_string(intent.source),
            Pubkey.from_string(intent.mint),
            Pubkey.from_string(intent.destination),
            Pubkey.from_string(intent.authority),
            int(amount),
            intent.decimals,
        )
    )
    assert instruction.data == struct.pack("<BQB", 12, int(amount), intent.decimals)
    assert [(str(a.pubkey), a.is_signer, a.is_writable) for a in instruction.accounts] == [
        (intent.source, False, True),
        (intent.mint, False, False),
        (intent.destination, False, True),
        (intent.authority, True, False),
    ]
    reference = Message.new_with_blockhash(
        [instruction],
        Pubkey.from_string(intent.fee_payer),
        Hash.from_string(compile_request.recent_blockhash),
    )
    assert reference.account_keys[0] == Pubkey.from_string(intent.fee_payer)
    assert reference.header.num_required_signatures == (
        1 if intent.authority == intent.fee_payer else 2
    )
    assert raw == bytes(reference)
    candidate = parse_signed_candidate(signer.encode(reference))
    bind_signed_candidate(intent, candidate, compile_request.genesis_hash)
    assert candidate.message_bytes == raw and len(candidate.first_signature) == 64
    assert candidate.transaction_digest == hashlib.sha256(candidate.signed_bytes).hexdigest()


@pytest.mark.parametrize(
    "field",
    [
        "mint",
        "source",
        "destination",
        "authority",
        "fee_payer",
        "decimals",
        "raw_amount",
        "genesis_hash",
    ],
)
def test_valid_native_candidate_binds_every_encoded_field(native_case, compile_request, field):
    intent, signer = native_case
    encoded = signer.encode(build_payout_message(intent, compile_request.recent_blockhash))
    candidate = parse_signed_candidate(encoded)
    replacement = {"raw_amount": "11", "decimals": 7}.get(field, str(Pubkey.new_unique()))
    changed = intent.model_copy(update={field: replacement})
    with pytest.raises(InvalidPayoutCandidateError):
        bind_signed_candidate(changed, candidate, compile_request.genesis_hash)


@pytest.mark.parametrize("mode", ["unsigned", "bad_signature", "bad_authority_signature"])
def test_real_signature_verification_rejects(native_case, compile_request, mode):
    intent, signer = native_case
    message = build_payout_message(intent, compile_request.recent_blockhash)
    with pytest.raises(InvalidPayoutCandidateError):
        parse_signed_candidate(signer.encode(message, mode))


def test_partial_signatures_rejected(compile_request):
    signer = SyntheticSigner(False)
    with pytest.raises(InvalidPayoutCandidateError):
        parse_signed_candidate(
            signer.encode(
                build_payout_message(signer.intent(), compile_request.recent_blockhash), "partial"
            )
        )


@pytest.mark.parametrize("wire", ["", " ", "AA==", "!!!!", "A" * 1648, "AA==\n", "AB=="])
def test_malformed_or_noncanonical_wire(wire):
    with pytest.raises(InvalidPayoutCandidateError):
        parse_signed_candidate(wire)


def test_trailing_bytes_and_version_zero(native_case, compile_request):
    intent, signer = native_case
    message = build_payout_message(intent, compile_request.recent_blockhash)
    wire = base64.b64decode(signer.encode(message))
    with pytest.raises(InvalidPayoutCandidateError):
        parse_signed_candidate(base64.b64encode(wire + b"x").decode())
    with pytest.raises(InvalidPayoutCandidateError):
        parse_signed_candidate(signer.encode(message, "v0"))


@pytest.mark.parametrize("extra", [False, True])
def test_other_program_and_extra_instruction_rejected(native_case, compile_request, extra):
    intent, signer = native_case
    instruction = transfer_checked(
        TransferCheckedParams(
            TOKEN_2022_PROGRAM_ID if extra else TOKEN_PROGRAM_ID,
            Pubkey.from_string(intent.source),
            Pubkey.from_string(intent.mint),
            Pubkey.from_string(intent.destination),
            Pubkey.from_string(intent.authority),
            10,
            6,
        )
    )
    message = Message.new_with_blockhash(
        [instruction] * (2 if extra else 1),
        Pubkey.from_string(intent.fee_payer),
        Hash.from_string(compile_request.recent_blockhash),
    )
    candidate = parse_signed_candidate(signer.encode(message))
    with pytest.raises(InvalidPayoutCandidateError):
        bind_signed_candidate(intent, candidate, intent.genesis_hash)


@pytest.mark.parametrize(
    "field,value",
    [("format_version", v) for v in [True, 1.0, "1", 0, 2]]
    + [("last_valid_block_height", v) for v in [True, 1, "01", "-1", "1e1", "18446744073709551616"]]
    + [("epoch", True), ("signature", "private-marker"), ("rpc_endpoint", "private-marker")],
)
def test_envelope_strict_input(compile_request, field, value):
    payload = compile_request.model_dump(mode="json", exclude={"recent_blockhash"})
    payload |= {"format_version": 1, "signed_transaction_base64": "AAAA", field: value}
    with pytest.raises(ValueError):
        SignedEnvelope.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "arguments",
    [
        ["prepare-message"],
        ["prepare-message", "--output"],
        ["prepare-message", "--output", "private-marker", "extra"],
        ["prepare-commit", "--output", "private-marker"],
    ],
)
def test_cli_bad_arguments_are_private(arguments):
    out = io.StringIO()
    assert main(arguments, io.StringIO("{}"), out) == 5
    assert json.loads(out.getvalue()) == {"verdict": "invalid_input"}

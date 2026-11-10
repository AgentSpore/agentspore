"""Confirm a persisted payout attempt from finalized receipt evidence; never sends or signs."""

import base64
import hashlib
import json

import base58
import httpx
from pydantic import AnyHttpUrl
from solders.hash import Hash
from solders.instruction import CompiledInstruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import Transaction

from app.repositories.payout_delivery_repo import PayoutDeliveryRepository
from app.schemas.payout_delivery import (
    ClaimKey,
    ConfirmationEvidence,
    ReceiptIntent,
    ReconcileRequest,
    ReconcileTarget,
    SignedCandidate,
    TokenBalance,
)
from app.services.payout_delivery_service import (
    RECEIPT_ERRORS,
    NetworkMismatchError,
    fetch_finalized_receipt,
    receipt_verdict,
)
from app.services.payout_transaction import (
    InvalidPayoutCandidateError,
    bind_signed_candidate,
    parse_signed_candidate,
)

VERIFIER_VERSION = 1


def wire_message(message: dict) -> bytes:
    """Rebuild the exact legacy message bytes from the RPC JSON message."""
    header = message["header"]
    instructions = [
        CompiledInstruction(
            item["programIdIndex"], base58.b58decode(item["data"]), bytes(item["accounts"])
        )
        for item in message["instructions"]
    ]
    return bytes(
        Message.new_with_compiled_instructions(
            header["numRequiredSignatures"],
            header["numReadonlySignedAccounts"],
            header["numReadonlyUnsignedAccounts"],
            [Pubkey.from_string(key) for key in message["accountKeys"]],
            Hash.from_string(message["recentBlockhash"]),
            instructions,
        )
    )


def exact_wire(receipt: dict, target: ReconcileTarget, message_bytes: bytes) -> bool:
    """Require the compiled message and every signature to equal the stored wire."""
    transaction = receipt["transaction"]
    stored = [str(sig) for sig in Transaction.from_bytes(target.signed_bytes).signatures]
    return (
        wire_message(transaction["message"]) == message_bytes
        and transaction["signatures"] == stored
    )


def build_evidence(receipt: dict) -> ConfirmationEvidence:
    """Digest the canonical verified facts; a malformed slot never validates."""
    slot, meta = receipt["slot"], receipt["meta"]
    if type(slot) is not int:
        raise TypeError
    balances = [
        [
            (b.account_index, b.mint, b.owner, b.program_id, b.token_amount.amount)
            for b in map(TokenBalance.model_validate, meta[name])
        ]
        for name in ("preTokenBalances", "postTokenBalances")
    ]
    canonical = json.dumps(
        {
            "version": VERIFIER_VERSION,
            "slot": slot,
            "signatures": receipt["transaction"]["signatures"],
            "fee": meta["fee"],
            "lamports": [meta["preBalances"], meta["postBalances"]],
            "tokens": balances,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return ConfirmationEvidence(
        finalized_slot=str(slot),
        verifier_version=VERIFIER_VERSION,
        evidence_digest=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def restore_candidate(target: ReconcileTarget) -> SignedCandidate:
    """Re-verify the stored bytes natively against the stored intent and first signature."""
    transfer = target.transfer
    candidate = parse_signed_candidate(base64.b64encode(target.signed_bytes).decode())
    bind_signed_candidate(transfer, candidate, transfer.genesis_hash)
    if candidate.first_signature != target.first_signature:
        raise InvalidPayoutCandidateError
    return candidate


def receipt_intent(target: ReconcileTarget, endpoint: AnyHttpUrl) -> ReceiptIntent:
    """Name the stored transfer, its first signature and the RPC to ask."""
    return ReceiptIntent(
        **target.transfer.model_dump(),
        signature=str(Signature.from_bytes(target.first_signature)),
        rpc_endpoint=endpoint,
    )


def fetch_evidence(
    client: httpx.Client, target: ReconcileTarget, request: ReconcileRequest
) -> tuple[str, ConfirmationEvidence | None]:
    """Re-verify stored bytes, then ask the trusted network; only `confirmed` carries evidence."""
    if request.trusted_genesis_hash != target.transfer.genesis_hash:
        return "unsupported", None
    try:
        candidate = restore_candidate(target)
    except InvalidPayoutCandidateError:
        return "storage_error", None
    intent = receipt_intent(target, request.rpc_endpoint)
    try:
        receipt = fetch_finalized_receipt(client, intent)
        return judge_receipt(receipt, intent, target, candidate.message_bytes)
    except NetworkMismatchError:
        return "unsupported", None
    except RECEIPT_ERRORS:
        return "unresolved", None


def judge_receipt(
    receipt: dict | None, intent: ReceiptIntent, target: ReconcileTarget, message_bytes: bytes
) -> tuple[str, ConfirmationEvidence | None]:
    """Only a matching receipt that is also the exact stored wire yields evidence."""
    if receipt is None:
        return "unresolved", None
    verdict = receipt_verdict(receipt, intent)
    if verdict != "matching":
        return verdict, None
    if not exact_wire(receipt, target, message_bytes):
        return "unsupported", None
    return "confirmed", build_evidence(receipt)


def confirmation_response(
    key: ClaimKey, target: ReconcileTarget, evidence: ConfirmationEvidence, verdict: str
) -> dict:
    """Project only identity, wire digest, slot and verifier version."""
    return {
        "verdict": verdict,
        "intent_id": str(key.intent_id),
        "state": "confirmed",
        "transaction_digest": target.transaction_digest,
        "finalized_slot": evidence.finalized_slot,
        "verifier_version": evidence.verifier_version,
    }


async def load_target(repo: PayoutDeliveryRepository, key: ClaimKey) -> ReconcileTarget | None:
    """Read persisted material only; the caller supplies no payload."""
    row = await repo.read_reconcile_row(key)
    return ReconcileTarget.from_row(row) if row else None


async def show_confirmation(repo: PayoutDeliveryRepository, key: ClaimKey) -> dict:
    """Return the stored fact, never a fresh RPC check."""
    target = await load_target(repo, key)
    if target is None or target.stored is None:
        return {"verdict": "not_found"}
    return confirmation_response(key, target, target.stored, "found")


async def record_confirmation(
    repo: PayoutDeliveryRepository,
    key: ClaimKey,
    target: ReconcileTarget,
    evidence: ConfirmationEvidence,
) -> dict:
    """Insert once under the parent lock; identical evidence replays, different never overwrites."""
    if await repo.lock_intent(key) is None:
        return {"verdict": "not_found"}
    current = await load_target(repo, key)
    if current is None or current.transaction_digest != target.transaction_digest:
        return {"verdict": "conflict"}
    if current.stored is None:
        await repo.insert_confirmation(key, evidence)
        return confirmation_response(key, current, evidence, "confirmed")
    verdict = "replayed" if current.stored == evidence else "conflict"
    return confirmation_response(key, current, current.stored, verdict)

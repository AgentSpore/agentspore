"""Read-only verification of the two deliberately narrow legacy payout receipt shapes."""

import hashlib
import struct
from datetime import UTC
from typing import Any

import base58
import httpx
from solders.signature import Signature

from app.repositories.payout_delivery_repo import PayoutDeliveryRepository
from app.schemas.payout_delivery import (
    ATA_PROGRAM,
    SYSTEM_PROGRAM,
    AllocationIntent,
    AllocationKey,
    ClaimKey,
    CompileRequest,
    ReceiptIntent,
    SignedEnvelope,
    TokenBalance,
    TransferIntent,
    solana_hash,
)
from app.services.payout_transaction import (
    bind_signed_candidate,
    compile_signing_request,
    parse_signed_candidate,
)


def rpc_result(client: httpx.Client, intent: ReceiptIntent, method: str, params: list) -> Any:
    """Fetch one JSON-RPC result, rejecting incomplete or erroneous envelopes."""
    return rpc_call(client, str(intent.rpc_endpoint), method, params)


def rpc_call(client: httpx.Client, endpoint: str, method: str, params: list) -> Any:
    """Post one JSON-RPC request to an explicit endpoint and return its checked result."""
    response = client.post(
        endpoint,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    response.raise_for_status()
    body = response.json()
    if (
        body["jsonrpc"] != "2.0"
        or type(body["id"]) is not int
        or body["id"] != 1
        or "error" in body
    ):
        raise ValueError
    return body["result"]


def message_flags(keys: list, header: dict) -> dict:
    """Validate byte counts and account bounds before deriving signer permissions."""
    required, readonly_signed, readonly_unsigned = (
        header[name]
        for name in (
            "numRequiredSignatures",
            "numReadonlySignedAccounts",
            "numReadonlyUnsignedAccounts",
        )
    )
    if any(
        type(value) is not int or not 0 <= value <= 255
        for value in (required, readonly_signed, readonly_unsigned)
    ):
        raise ValueError
    if (
        not 1 <= required <= len(keys)
        or readonly_signed > required
        or readonly_unsigned > len(keys) - required
    ):
        raise ValueError
    return {
        key: (
            i < required,
            i < required - readonly_signed if i < required else i < len(keys) - readonly_unsigned,
        )
        for i, key in enumerate(keys)
    }


def expected_message(intent: ReceiptIntent, create: bool) -> tuple[dict, list]:
    """Return the exact account flags and instructions of shape A, or B when `create`."""
    flags = {
        intent.program: (False, False),
        intent.mint: (False, False),
        intent.source: (False, True),
        intent.destination: (False, True),
        intent.authority: (True, intent.authority == intent.fee_payer),
        intent.fee_payer: (True, True),
    }
    instructions = [
        (
            [intent.program, intent.source, intent.mint, intent.destination, intent.authority],
            struct.pack("<BQB", 12, int(intent.raw_amount), intent.decimals),
        )
    ]
    if create:
        flags |= {
            intent.recipient: (False, False),
            SYSTEM_PROGRAM: (False, False),
            ATA_PROGRAM: (False, False),
        }
        instructions.insert(
            0,
            (
                [
                    ATA_PROGRAM,
                    intent.fee_payer,
                    intent.destination,
                    intent.recipient,
                    intent.mint,
                    SYSTEM_PROGRAM,
                    intent.program,
                ],
                b"\x01",
            ),
        )
    return flags, instructions


def message_matches(message: dict, intent: ReceiptIntent, create: bool = False) -> bool:
    """Bind compiled accounts, payer, flags and instruction order to the exact payout shape."""
    keys = message["accountKeys"]
    solana_hash(message["recentBlockhash"])
    flags = message_flags(keys, message["header"])
    actual = []
    for instruction in message["instructions"]:
        if not isinstance(instruction["data"], str):
            raise TypeError
        indices = [instruction["programIdIndex"], *instruction["accounts"]]
        if any(type(index) is not int or not 0 <= index < len(keys) for index in indices):
            raise ValueError
        actual.append(([keys[index] for index in indices], base58.b58decode(instruction["data"])))
    expected, expected_instructions = expected_message(intent, create)
    if len(keys) != len(expected) or flags != expected or keys[0] != intent.fee_payer:
        return False
    return actual == expected_instructions


def inner_instructions_match(inner: list, keys: list, intent: ReceiptIntent, create: bool) -> bool:
    """Shape A runs no CPI; shape B only System and Token-2022 (ATA creation and its init)."""
    if not create:
        return inner == []
    allowed = {SYSTEM_PROGRAM, intent.program}
    indices = [ix["programIdIndex"] for group in inner for ix in group["instructions"]]
    if any(type(index) is not int or not 0 <= index < len(keys) for index in indices):
        raise ValueError
    return all(keys[index] in allowed for index in indices)


def token_snapshot(
    balances: list, keys: list, intent: ReceiptIntent, new_destination: bool = False
) -> dict | None:
    """Require both complete token balances; a destination created in-flight counts as 0."""
    result = {}
    for raw in balances:
        balance = TokenBalance.model_validate(raw)
        key = keys[balance.account_index]
        owner = {intent.source: intent.authority, intent.destination: intent.recipient}.get(key)
        if key in result or (
            balance.mint,
            balance.program_id,
            balance.owner,
            balance.token_amount.decimals,
        ) != (intent.mint, intent.program, owner, intent.decimals):
            return None
        result[key] = int(balance.token_amount.amount)
    if new_destination:
        result.setdefault(intent.destination, 0)
    if set(result) != {intent.source, intent.destination}:
        raise ValueError
    return result


def lamports_match(meta: dict, keys: list, intent: ReceiptIntent, create: bool = False) -> bool:
    """Require complete lamport deltas: the fee, plus in shape B the rent paid into the ATA."""
    pre, post, fee = meta["preBalances"], meta["postBalances"], meta["fee"]
    if any(type(value) is not int or value < 0 for value in [*pre, *post, fee]):
        raise ValueError
    if len(pre) != len(keys):
        return False
    rent, expected = 0, list(pre)
    if create:
        dest = keys.index(intent.destination)
        rent = post[dest] - pre[dest]
        expected[dest] += rent
    expected[0] -= fee + rent
    return rent >= 0 and post == expected


def receipt_verdict(receipt: dict, intent: ReceiptIntent) -> str:
    """Compare finalized RPC evidence without inferring missing evidence."""
    meta, transaction = receipt["meta"], receipt["transaction"]
    signatures = transaction["signatures"]
    for signature in signatures:
        Signature.from_string(signature)
    if signatures[0] != intent.signature:
        return "unsupported"
    if meta["err"] is not None:
        if not isinstance(meta["err"], (str, dict)) or not meta["err"]:
            raise ValueError
        return "failed"
    message = transaction["message"]
    count = len(message["instructions"])
    if not count:
        raise ValueError
    create = count == 2
    keys = message["accountKeys"]
    if (
        receipt["version"] != "legacy"
        or count not in (1, 2)
        or not inner_instructions_match(meta["innerInstructions"], keys, intent, create)
        or not message_matches(message, intent, create)
        or len(signatures) != message["header"]["numRequiredSignatures"]
        or not lamports_match(meta, keys, intent, create)
    ):
        return "unsupported"
    before = token_snapshot(meta["preTokenBalances"], keys, intent, create)
    after = token_snapshot(meta["postTokenBalances"], keys, intent)
    amount = int(intent.raw_amount)
    return (
        "matching"
        if before is not None
        and after is not None
        and (
            before[intent.source] - after[intent.source],
            after[intent.destination] - before[intent.destination],
        )
        == (amount, amount)
        else "unsupported"
    )


class NetworkMismatchError(Exception):
    """The RPC endpoint reports a genesis hash other than the intent's network."""


RECEIPT_ERRORS = (
    httpx.HTTPError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    OverflowError,
    RecursionError,
)


def fetch_finalized_receipt(client: httpx.Client, intent: ReceiptIntent) -> Any:
    """Bind the RPC to the intent's network, then fetch the finalized transaction."""
    if rpc_result(client, intent, "getGenesisHash", []) != intent.genesis_hash:
        raise NetworkMismatchError
    return rpc_result(
        client,
        intent,
        "getTransaction",
        [
            intent.signature,
            {"commitment": "finalized", "encoding": "json", "maxSupportedTransactionVersion": 0},
        ],
    )


def verify_receipt(client: httpx.Client, intent: ReceiptIntent) -> str:
    """Return a finite verdict; transport or incomplete evidence stays unresolved."""
    try:
        receipt = fetch_finalized_receipt(client, intent)
        return "unresolved" if receipt is None else receipt_verdict(receipt, intent)
    except NetworkMismatchError:
        return "unsupported"
    except RECEIPT_ERRORS:
        return "unresolved"


async def admit_intent(repo: PayoutDeliveryRepository, intent: AllocationIntent) -> dict:
    """Admit or compare an allocation without committing the caller's transaction."""
    row = await repo.insert(intent)
    if row is not None:
        return {"verdict": "accepted", "intent_id": str(row["intent_id"]), "state": row["state"]}
    # INVARIANT(188): a separate READ COMMITTED statement must see the committed winner.
    row = await repo.read(
        AllocationKey.model_validate(
            intent.model_dump(include={"producer_namespace", "allocation_id"})
        )
    )
    if row is None:
        return {"verdict": "storage_error"}
    if any(row[name] != value for name, value in intent.model_dump().items()):
        return {"verdict": "conflict"}
    return {"verdict": "replayed", "intent_id": str(row["intent_id"]), "state": row["state"]}


async def show_intent(repo: PayoutDeliveryRepository, key: AllocationKey) -> dict:
    """Return only the durable allocation identity and admitted state."""
    row = await repo.read(key)
    if row is None:
        return {"verdict": "not_found"}
    return {"verdict": "found", "intent_id": str(row["intent_id"]), "state": row["state"]}


async def manage_claim(repo: PayoutDeliveryRepository, command: str, request: ClaimKey) -> dict:
    """Return observed lease facts without owning the caller's transaction."""
    if command == "lease-show":
        row = await repo.read_claim(request)
        verdict = "found"
        missing = "not_found"
    else:
        parent, row = await repo.mutate_claim(command, request)
        if not parent:
            return {"verdict": "not_found"}
        verdict = {
            "claim": "claimed",
            "renew": "renewed",
            "release": "released",
            "recover": "recovered",
        }[command]
        missing = "unavailable" if command == "claim" else "stale"
    if row is None:
        return {"verdict": missing}
    expiry = row["expires_at"]
    return {
        "verdict": verdict,
        "intent_id": str(row["intent_id"]),
        "state": row["state"],
        "epoch": row["epoch"],
        "worker_id": str(row["worker_id"]) if row["worker_id"] else None,
        "expires_at": expiry.astimezone(UTC).isoformat() if expiry else None,
    }


async def compile_payout(
    repo: PayoutDeliveryRepository, request: CompileRequest, create_ata: bool = False
) -> dict:
    """Read an actual live fence and compile only to an internal private-artifact result."""
    parent = await repo.lock_intent(request)
    if parent is None:
        return {"verdict": "not_found"}
    if not await repo.live_preparation_fence(request):
        return {"verdict": "stale"}
    intent = TransferIntent.model_validate(
        {name: parent[name] for name in TransferIntent.model_fields}
    )
    artifact = compile_signing_request(intent, request, create_ata)
    return {
        "verdict": "compiled",
        "intent_id": str(request.intent_id),
        "worker_id": str(request.worker_id),
        "epoch": request.epoch,
        "message_digest": artifact.message_digest,
        "_signing_request": artifact.model_dump_json(),
    }


async def list_pending(repo: PayoutDeliveryRepository, limit: int) -> dict:
    """List intent identifiers with their delivery phase; nothing else leaves the database."""
    rows = await repo.list_phases(limit)
    return {
        "verdict": "found",
        "intents": [{"intent_id": str(row["intent_id"]), "phase": row["phase"]} for row in rows],
    }


async def read_transfer(repo: PayoutDeliveryRepository, key: ClaimKey) -> TransferIntent | None:
    """Read the immutable transfer of one intent, or None when it does not exist."""
    parent = await repo.lock_intent(key)
    if parent is None:
        return None
    return TransferIntent.model_validate(
        {name: parent[name] for name in TransferIntent.model_fields}
    )


def attempt_response(row: dict, verdict: str) -> dict:
    """Project only technical identity and exact-wire digest to ordinary output."""
    return {
        "verdict": verdict,
        "intent_id": str(row["intent_id"]),
        "state": "prepared",
        "worker_id": str(row["worker_id"]),
        "epoch": row["epoch"],
        "transaction_digest": hashlib.sha256(bytes(row["signed_bytes"])).hexdigest(),
    }


async def prepare_attempt(repo: PayoutDeliveryRepository, envelope: SignedEnvelope) -> dict:
    """Verify exact native material, then atomically retain one live-fenced attempt."""
    candidate = parse_signed_candidate(envelope.signed_transaction_base64)
    parent = await repo.lock_intent(envelope)
    if parent is None:
        return {"verdict": "not_found"}
    intent = TransferIntent.model_validate(
        {name: parent[name] for name in TransferIntent.model_fields}
    )
    bind_signed_candidate(intent, candidate, envelope.genesis_hash)
    values = {
        "intent_id": envelope.intent_id,
        "epoch": envelope.epoch,
        "worker_id": envelope.worker_id,
        "signed_bytes": candidate.signed_bytes,
        "first_signature": candidate.first_signature,
        "recent_blockhash": candidate.recent_blockhash,
        "last_valid_block_height": envelope.last_valid_block_height,
    }
    previous = await repo.read_attempt(envelope)
    if previous is not None:
        if any(previous[name] != value for name, value in values.items()):
            return {"verdict": "conflict"}
        return attempt_response(previous, "replayed")
    if not await repo.prepare_fence(envelope):
        return {"verdict": "stale"}
    await repo.insert_attempt(values)
    return attempt_response(values, "prepared")


async def show_attempt(repo: PayoutDeliveryRepository, key: ClaimKey) -> dict:
    """Read committed original identity after an uncertain acknowledgement."""
    row = await repo.read_attempt(key)
    return attempt_response(row, "found") if row else {"verdict": "not_found"}


async def handle_payout_request(
    repo: PayoutDeliveryRepository,
    command: str,
    request: AllocationKey | AllocationIntent | ClaimKey,
    create_ata: bool = False,
) -> dict:
    """Dispatch validated operator requests to the actual domain operations."""
    if isinstance(request, SignedEnvelope):
        return await prepare_attempt(repo, request)
    if isinstance(request, CompileRequest):
        return await compile_payout(repo, request, create_ata)
    if command == "attempt-show" and isinstance(request, ClaimKey):
        return await show_attempt(repo, request)
    if isinstance(request, ClaimKey):
        return await manage_claim(repo, command, request)
    if isinstance(request, AllocationIntent):
        return await admit_intent(repo, request)
    return await show_intent(repo, request)

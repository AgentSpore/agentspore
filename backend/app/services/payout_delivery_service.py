"""Read-only verification of the deliberately narrow legacy payout receipt shape."""

import struct
from datetime import UTC
from typing import Any

import base58
import httpx
from solders.signature import Signature

from app.repositories.payout_delivery_repo import PayoutDeliveryRepository
from app.schemas.payout_delivery import (
    AllocationIntent,
    AllocationKey,
    ClaimKey,
    ReceiptIntent,
    TokenBalance,
    solana_hash,
)


def rpc_result(client: httpx.Client, intent: ReceiptIntent, method: str, params: list) -> Any:
    """Fetch one JSON-RPC result, rejecting incomplete or erroneous envelopes."""
    response = client.post(
        str(intent.rpc_endpoint),
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


def message_matches(message: dict, intent: ReceiptIntent) -> bool:
    """Bind compiled accounts, payer and signer/writable flags to the exact transfer."""
    keys = message["accountKeys"]
    solana_hash(message["recentBlockhash"])
    flags = message_flags(keys, message["header"])
    expected = {
        intent.program: (False, False),
        intent.mint: (False, False),
        intent.source: (False, True),
        intent.destination: (False, True),
        intent.authority: (True, intent.authority == intent.fee_payer),
        intent.fee_payer: (True, True),
    }
    if len(keys) != len(expected) or flags != expected or keys[0] != intent.fee_payer:
        return False
    (instruction,) = message["instructions"]
    if not isinstance(instruction["data"], str):
        raise TypeError
    indices = [instruction["programIdIndex"], *instruction["accounts"]]
    if any(type(index) is not int or not 0 <= index < len(keys) for index in indices):
        raise ValueError
    accounts = [keys[index] for index in indices]
    return accounts == [
        intent.program,
        intent.source,
        intent.mint,
        intent.destination,
        intent.authority,
    ] and base58.b58decode(instruction["data"]) == struct.pack(
        "<BQB", 12, int(intent.raw_amount), intent.decimals
    )


def token_snapshot(balances: list, keys: list, intent: ReceiptIntent) -> dict | None:
    """Require exactly the two complete historical token balances."""
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
    if set(result) != {intent.source, intent.destination}:
        raise ValueError
    return result


def lamports_match(meta: dict, keys: list) -> bool:
    """Require complete lamport deltas containing only the transaction fee."""
    pre, post, fee = meta["preBalances"], meta["postBalances"], meta["fee"]
    if any(type(value) is not int or value < 0 for value in [*pre, *post, fee]):
        raise ValueError
    return len(pre) == len(keys) and post == [pre[0] - fee, *pre[1:]]


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
    if receipt["version"] != "legacy" or meta["innerInstructions"] != []:
        return "unsupported"
    message = transaction["message"]
    if (
        not message_matches(message, intent)
        or len(signatures) != message["header"]["numRequiredSignatures"]
    ):
        return "unsupported"
    keys = message["accountKeys"]
    if not lamports_match(meta, keys):
        return "unsupported"
    before = token_snapshot(meta["preTokenBalances"], keys, intent)
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


def verify_receipt(client: httpx.Client, intent: ReceiptIntent) -> str:
    """Return a finite verdict; transport or incomplete evidence stays unresolved."""
    try:
        if rpc_result(client, intent, "getGenesisHash", []) != intent.genesis_hash:
            return "unsupported"
        receipt = rpc_result(
            client,
            intent,
            "getTransaction",
            [
                intent.signature,
                {
                    "commitment": "finalized",
                    "encoding": "json",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        return "unresolved" if receipt is None else receipt_verdict(receipt, intent)
    except (
        httpx.HTTPError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OverflowError,
        RecursionError,
    ):
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

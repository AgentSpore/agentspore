"""Broadcast the exact persisted signed transaction of a prepared attempt; never re-signs."""

import base64
from typing import Any

import httpx

from app.schemas.payout_delivery import ReceiptIntent, ReconcileTarget, SendRequest
from app.services.payout_delivery_service import (
    RECEIPT_ERRORS,
    NetworkMismatchError,
    rpc_result,
)
from app.services.payout_reconciliation import receipt_intent, restore_candidate
from app.services.payout_transaction import InvalidPayoutCandidateError

SEND_OPTIONS = {
    "encoding": "base64",
    "skipPreflight": False,
    "preflightCommitment": "finalized",
    "maxRetries": 0,
}
MINT_OPTIONS = {"encoding": "jsonParsed", "commitment": "finalized"}
ALLOWED_MINT_EXTENSIONS = frozenset({"metadataPointer", "tokenMetadata"})
PREFLIGHT_FAILURE_CODE = -32002
SEND_ERRORS = (*RECEIPT_ERRORS, AttributeError)


def classify_send_body(body: dict, signature: str) -> str:
    """Map one JSON-RPC envelope to a verdict; anything ambiguous stays unresolved."""
    if body["jsonrpc"] != "2.0" or type(body["id"]) is not int or body["id"] != 1:
        raise ValueError
    error = body.get("error")
    if error is None:
        result = body["result"]
        if type(result) is not str:
            raise ValueError
        return "sent" if result == signature else "unsupported"
    if not isinstance(error, dict):
        raise ValueError
    data = error.get("data")
    detail = data.get("err") if isinstance(data, dict) else None
    if detail == "AlreadyProcessed" or "already been processed" in str(error.get("message")):
        return "already_processed"
    if detail is not None and detail != "BlockhashNotFound":
        return "preflight_rejected" if error.get("code") == PREFLIGHT_FAILURE_CODE else "unresolved"
    return "unresolved"


def mint_supported(account: Any, intent: ReceiptIntent) -> bool:
    """Accept only a Token-2022 mint with the intent's decimals and allowlisted extensions."""
    value = account["value"]
    if value is None:
        raise ValueError
    parsed = value["data"]["parsed"]
    info = parsed["info"]
    extensions = info.get("extensions", [])
    if type(extensions) is not list:
        raise TypeError
    names = {item["extension"] for item in extensions}
    return (
        value["owner"] == intent.program
        and parsed["type"] == "mint"
        and info["decimals"] == intent.decimals
        and names <= ALLOWED_MINT_EXTENSIONS
    )


def send_stored_wire(client: httpx.Client, target: ReconcileTarget, request: SendRequest) -> str:
    """Re-verify stored bytes, bind the RPC and the mint, then send the stored wire unchanged."""
    if request.trusted_genesis_hash != target.transfer.genesis_hash:
        return "unsupported"
    try:
        restore_candidate(target)
    except InvalidPayoutCandidateError:
        return "storage_error"
    intent = receipt_intent(target, request.rpc_endpoint)
    try:
        if rpc_result(client, intent, "getGenesisHash", []) != intent.genesis_hash:
            raise NetworkMismatchError
        mint = rpc_result(client, intent, "getAccountInfo", [intent.mint, MINT_OPTIONS])
        if not mint_supported(mint, intent):
            raise NetworkMismatchError
        wire = base64.b64encode(target.signed_bytes).decode()
        response = client.post(
            str(request.rpc_endpoint),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [wire, SEND_OPTIONS],
            },
        )
        # Preflight failures may arrive with a non-2xx status; the JSON-RPC body decides.
        return classify_send_body(response.json(), intent.signature)
    except NetworkMismatchError:
        return "unsupported"
    except SEND_ERRORS:
        return "unresolved"

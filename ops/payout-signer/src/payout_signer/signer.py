"""Sign command: validate, enforce limits, record, then write the signed envelope."""

import base64
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from solders.message import Message
from solders.transaction import Transaction

from payout_signer.config import Config, ConfigError, load_config, load_keypair
from payout_signer.ledger import Ledger
from payout_signer.limits import Transfer, within_limits
from payout_signer.message import RejectedMessageError, validate_message
from payout_signer.verdict import Verdict

MAX_REQUEST_BYTES = 16384
CONTEXT_KEYS = (
    "intent_id",
    "worker_id",
    "epoch",
    "genesis_hash",
    "last_valid_block_height",
    "format_version",
)
REQUEST_KEYS = {*CONTEXT_KEYS, "recent_blockhash", "unsigned_message_base64", "message_digest"}
HEIGHT = re.compile(r"^(0|[1-9][0-9]{0,19})$")


class RejectedRequestError(Exception):
    """The request file is not the exact signing request shape."""


def read_request(path: Path) -> dict:
    """Read a bounded, strictly shaped request without following symlinks."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(MAX_REQUEST_BYTES + 1)
        if len(data) > MAX_REQUEST_BYTES:
            raise RejectedRequestError
        body = json.loads(data)
    except (OSError, ValueError, RecursionError):
        raise RejectedRequestError from None
    strings = ("intent_id", "worker_id", "genesis_hash", "recent_blockhash", "message_digest")
    valid = (
        isinstance(body, dict)
        and set(body) == REQUEST_KEYS
        and all(isinstance(body[k], str) for k in (*strings, "unsigned_message_base64"))
        and type(body["epoch"]) is int
        and type(body["format_version"]) is int
        and body["format_version"] == 1
        and isinstance(body["last_valid_block_height"], str)
        and HEIGHT.match(body["last_valid_block_height"]) is not None
    )
    if not valid:
        raise RejectedRequestError
    return body


def _decode_message(body: dict, config: Config, signer) -> tuple[bytes, Transfer]:
    try:
        raw = base64.b64decode(body["unsigned_message_base64"], validate=True)
    except ValueError:
        raise RejectedRequestError from None
    if not 1 <= len(raw) <= 1232 or hashlib.sha256(raw).hexdigest() != body["message_digest"]:
        raise RejectedRequestError
    transfer = validate_message(raw, signer.pubkey(), config.mint, config.decimals)
    if str(Message.from_bytes(raw).recent_blockhash) != body["recent_blockhash"]:
        raise RejectedMessageError
    return raw, transfer


def _write_envelope(output: Path, body: dict, raw: bytes, signer) -> None:
    message = Message.from_bytes(raw)
    signed = Transaction([signer], message, message.recent_blockhash)
    envelope = {k: body[k] for k in CONTEXT_KEYS}
    envelope["signed_transaction_base64"] = base64.b64encode(bytes(signed)).decode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(output, flags, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(envelope))
        handle.flush()
        os.fsync(handle.fileno())


class Refusal(Exception):
    """Stop the flow with a specific verdict."""

    def __init__(self, verdict: Verdict) -> None:
        super().__init__(verdict.label)
        self.verdict = verdict


def _sign(config_path: Path, request_path: Path, output_path: Path, now: float) -> Verdict:
    config = load_config(config_path)
    if os.path.lexists(config.pause_file):
        raise Refusal(Verdict.PAUSED)
    body = read_request(request_path)
    signer = load_keypair(config)
    raw, transfer = _decode_message(body, config, signer)
    if os.path.lexists(output_path):
        raise Refusal(Verdict.REJECTED)
    with Ledger.locked(config.ledger_path) as ledger:
        if config.paused:  # pausing while we waited for the lock must still win
            raise Refusal(Verdict.PAUSED)
        replay = ledger.has(body["message_digest"])
        if not replay and not within_limits(config, ledger.usage(now), transfer):
            raise Refusal(Verdict.LIMIT_EXCEEDED)
        if not replay:
            ledger.record(body["message_digest"], transfer.recipient, transfer.amount, now)
        _write_envelope(output_path, body, raw, signer)
    return Verdict.REPLAYED if replay else Verdict.SIGNED


def sign_request(
    config_path: Path, request_path: Path, output_path: Path, now: float | None = None
) -> Verdict:
    """Run the whole sign flow; every refusal is a verdict, never a leaked detail."""
    try:
        return _sign(config_path, request_path, output_path, time.time() if now is None else now)
    except Refusal as refusal:
        return refusal.verdict
    except ConfigError:
        return Verdict.CONFIG_ERROR
    except (RejectedRequestError, RejectedMessageError, FileExistsError):
        return Verdict.REJECTED
    except (OSError, sqlite3.Error):
        return Verdict.CONFIG_ERROR

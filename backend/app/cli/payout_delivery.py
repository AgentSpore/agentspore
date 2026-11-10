"""Verify one explicit payout intent from stdin; emit only a fixed JSON verdict."""

import asyncio
import json
import os
import sys
from typing import TextIO

import httpx
from asyncpg import exceptions as pg_errors
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.repositories.payout_delivery_repo import PayoutDeliveryRepository
from app.schemas.payout_delivery import (
    AllocationIntent,
    AllocationKey,
    ClaimFence,
    ClaimKey,
    ClaimRequest,
    CompileRequest,
    NetworkConfig,
    PendingRequest,
    ReceiptIntent,
    ReconcileRequest,
    RenewRequest,
    SendRequest,
    SignedEnvelope,
)
from app.services.payout_delivery_service import (
    RECEIPT_ERRORS,
    NetworkMismatchError,
    handle_payout_request,
    list_pending,
    read_transfer,
    verify_receipt,
)
from app.services.payout_network_config import (
    ConfigurationError,
    fetch_live_blockhash,
    intent_matches_config,
    load_network_config,
)
from app.services.payout_reconciliation import (
    fetch_evidence,
    load_target,
    record_confirmation,
    show_confirmation,
)
from app.services.payout_send import send_stored_wire
from app.services.payout_transaction import InvalidPayoutCandidateError, parse_signed_candidate

ADMISSION_EXIT_CODES = {
    "compiled": 0,
    "prepared": 0,
    "accepted": 0,
    "replayed": 0,
    "found": 0,
    "claimed": 0,
    "renewed": 0,
    "released": 0,
    "recovered": 0,
    "confirmed": 0,
    "sent": 0,
    "already_processed": 0,
    "unresolved": 2,
    "unsupported": 3,
    "failed": 4,
    "invalid_input": 5,
    "not_found": 6,
    "conflict": 7,
    "storage_error": 8,
    "busy": 9,
    "stale": 10,
    "unavailable": 11,
    "already_confirmed": 12,
    "preflight_rejected": 13,
    "configuration_error": 14,
}


# Supported configuration, SQLAlchemy, raw driver and transport failures stay private.
ADMISSION_ERRORS = (
    SQLAlchemyError,
    pg_errors.PostgresError,
    pg_errors.InterfaceError,
    pg_errors.InternalClientError,
    OSError,
    ValueError,
    TypeError,
    RuntimeError,
    ImportError,
    OverflowError,
)


def run(source: TextIO, destination: TextIO, transport: httpx.BaseTransport | None = None) -> int:
    """Own input validation, HTTP lifetime and machine-readable output."""
    try:
        payload = source.read(65537)
        intent = ReceiptIntent.model_validate_json(payload if len(payload) <= 65536 else "")
        with httpx.Client(timeout=10, transport=transport, trust_env=False) as client:
            verdict = verify_receipt(client, intent)
    except (ValueError, TypeError, OSError, RecursionError):
        verdict = "invalid_input"
    destination.write(json.dumps({"verdict": verdict}) + "\n")
    return {"matching": 0, "unresolved": 2, "unsupported": 3, "failed": 4, "invalid_input": 5}[
        verdict
    ]


async def reconcile_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    request: ReconcileRequest,
    transport: httpx.BaseTransport | None,
) -> dict:
    """Read, ask the network and write in separate transactions; RPC holds no lock."""
    async with session_factory.begin() as db:
        target = await load_target(PayoutDeliveryRepository(db), request)
    if target is None:
        return {"verdict": "not_found"}
    with httpx.Client(timeout=10, transport=transport, trust_env=False) as client:
        verdict, evidence = await asyncio.to_thread(fetch_evidence, client, target, request)
    if evidence is None:
        # The fresh outcome is reported as is; a stored confirmation is never touched and is
        # read back with confirmation-show.
        return {"verdict": verdict}
    async with session_factory.begin() as db:
        return await record_confirmation(PayoutDeliveryRepository(db), request, target, evidence)


async def send_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    request: SendRequest,
    transport: httpx.BaseTransport | None,
) -> dict:
    """Read in a short transaction, broadcast outside any transaction, write nothing."""
    async with session_factory.begin() as db:
        target = await load_target(PayoutDeliveryRepository(db), request)
    if target is None:
        return {"verdict": "not_found"}
    if target.stored is not None:
        return {"verdict": "already_confirmed"}
    with httpx.Client(timeout=10, transport=transport, trust_env=False) as client:
        return {"verdict": await asyncio.to_thread(send_stored_wire, client, target, request)}


async def auto_compile_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    fence: ClaimFence,
    config: NetworkConfig,
    transport: httpx.BaseTransport | None,
) -> dict:
    """Bind the persisted transfer and live network to server config, then compile."""
    async with session_factory.begin() as db:
        transfer = await read_transfer(PayoutDeliveryRepository(db), fence)
    if transfer is None:
        return {"verdict": "not_found"}
    if not intent_matches_config(transfer, config):
        return {"verdict": "unsupported"}
    try:
        with httpx.Client(timeout=10, transport=transport, trust_env=False) as client:
            blockhash, height = await asyncio.to_thread(fetch_live_blockhash, client, config)
        request = CompileRequest(
            **fence.model_dump(),
            genesis_hash=config.trusted_genesis_hash,
            recent_blockhash=blockhash,
            last_valid_block_height=height,
        )
    except NetworkMismatchError:
        return {"verdict": "unsupported"}
    except RECEIPT_ERRORS:
        return {"verdict": "unresolved"}
    async with session_factory.begin() as db:
        return await handle_payout_request(PayoutDeliveryRepository(db), "prepare-message", request)


async def dispatch_command(
    command: str,
    intent: AllocationKey | AllocationIntent | ClaimKey | PendingRequest,
    session_factory: async_sessionmaker[AsyncSession],
    transport: httpx.BaseTransport | None,
    config: NetworkConfig | None = None,
) -> dict:
    """Run one command in its own caller-owned transaction, or the split reconcile flow."""
    if isinstance(intent, PendingRequest):
        async with session_factory.begin() as db:
            return await list_pending(PayoutDeliveryRepository(db), intent.limit)
    if isinstance(intent, ClaimFence) and config is not None and command == "auto-compile":
        return await auto_compile_attempt(session_factory, intent, config, transport)
    if isinstance(intent, ReconcileRequest):
        return await reconcile_attempt(session_factory, intent, transport)
    if isinstance(intent, SendRequest):
        return await send_attempt(session_factory, intent, transport)
    async with session_factory.begin() as db:
        repo = PayoutDeliveryRepository(db)
        if command == "confirmation-show" and isinstance(intent, ClaimKey):
            return await show_confirmation(repo, intent)
        return await handle_payout_request(repo, command, intent)


async def execute_admission(
    command: str,
    intent: AllocationKey | AllocationIntent | ClaimKey | PendingRequest,
    session_factory: async_sessionmaker[AsyncSession] | None,
    transport: httpx.BaseTransport | None = None,
    config: NetworkConfig | None = None,
) -> dict:
    """Own transaction completion and dispose only a locally created engine."""
    engine = None
    try:
        if session_factory is None:
            engine = create_async_engine(get_settings().database_url, echo=False)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
        result = await dispatch_command(command, intent, session_factory, transport, config)
    except InvalidPayoutCandidateError:
        result = {"verdict": "invalid_input"}
    except DBAPIError as error:
        state = getattr(error.orig, "sqlstate", None)
        constraint = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
        verdict = (
            "busy"
            if isinstance(intent, ClaimKey) and state in {"55P03", "57014"}
            else "storage_error"
        )
        if (
            isinstance(intent, SignedEnvelope)
            and state == "23505"
            and constraint == "payout_attempt_signature_unique"
        ):
            verdict = "conflict"
        result = {"verdict": verdict}
    except ADMISSION_ERRORS:
        # The process boundary must never expose SQL, connection URLs or payloads.
        result = {"verdict": "storage_error"}
    finally:
        if engine is not None:
            try:
                await engine.dispose()
            except ADMISSION_ERRORS:
                result = {"verdict": "storage_error"}
    return result


async def run_admission(
    command: str,
    source: TextIO,
    destination: TextIO,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    output_path: str | None = None,
) -> int:
    """Validate bounded input and emit only facts from a completed transaction."""
    try:
        if (command == "prepare-message") != (output_path is not None):
            raise ValueError
        payload = source.read(65537)
        model = {
            "prepare-message": CompileRequest,
            "prepare-commit": SignedEnvelope,
            "attempt-show": ClaimKey,
            "confirmation-show": ClaimKey,
            "submit": AllocationIntent,
            "show": AllocationKey,
            "claim": ClaimRequest,
            "renew": RenewRequest,
            "release": ClaimFence,
            "recover": ClaimFence,
            "lease-show": ClaimKey,
        }[command]
        intent = model.model_validate_json(payload if len(payload) <= 65536 else "")
        if isinstance(intent, SignedEnvelope):
            parse_signed_candidate(intent.signed_transaction_base64)
    except (KeyError, ValueError, TypeError, OSError, RecursionError):
        result = {"verdict": "invalid_input"}
    else:
        result = await execute_admission(command, intent, session_factory)
    return emit_result(result, destination, output_path)


def emit_result(result: dict, destination: TextIO, output_path: str | None = None) -> int:
    """Write any private signing request exclusively, then only the fixed verdict JSON."""
    material = result.pop("_signing_request", None)
    if material is not None:
        try:
            write_signing_request(output_path, material)
        except (OSError, ValueError, TypeError):
            result = {"verdict": "invalid_input"}
    destination.write(json.dumps(result) + "\n")
    return ADMISSION_EXIT_CODES[result["verdict"]]


def configured_network() -> NetworkConfig | None:
    """Load the server network config; None means absent or unsafe, with no detail kept."""
    try:
        return load_network_config(get_settings().payout_network_config_path)
    except ConfigurationError:
        return None


def read_keyed_request(
    source: TextIO, model: type[ClaimKey]
) -> tuple[ClaimKey | None, NetworkConfig | None, str]:
    """Parse the bounded stdin reference, then load config; a failing verdict names the reason."""
    try:
        payload = source.read(65537)
        request = model.model_validate_json(payload if len(payload) <= 65536 else "")
    except (ValueError, TypeError, OSError, RecursionError):
        return None, None, "invalid_input"
    config = configured_network()
    return request, config, "" if config else "configuration_error"


async def run_auto_compile(
    source: TextIO,
    destination: TextIO,
    output_path: str,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Compile with the network taken from server config; stdin names only the fence."""
    fence, config, failure = read_keyed_request(source, ClaimFence)
    if failure or fence is None or config is None:
        return emit_result({"verdict": failure}, destination)
    result = await execute_admission("auto-compile", fence, session_factory, transport, config)
    return emit_result(result, destination, output_path)


async def run_auto(
    command: str,
    source: TextIO,
    destination: TextIO,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Send or reconcile one attempt with the network taken from server config."""
    key, config, failure = read_keyed_request(source, ClaimKey)
    if failure or key is None or config is None:
        return emit_result({"verdict": failure}, destination)
    model, inner = (
        (SendRequest, "attempt-send")
        if command == "auto-send"
        else (ReconcileRequest, "attempt-reconcile")
    )
    request = model(
        intent_id=key.intent_id,
        trusted_genesis_hash=config.trusted_genesis_hash,
        rpc_endpoint=config.rpc_endpoint,
    )
    return emit_result(
        await execute_admission(inner, request, session_factory, transport), destination
    )


async def run_pending(
    destination: TextIO,
    limit: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """List intent identifiers and phases, up to a bounded count."""
    try:
        request = PendingRequest.model_validate({"limit": limit})
    except ValueError:
        return emit_result({"verdict": "invalid_input"}, destination)
    return emit_result(await execute_admission("pending", request, session_factory), destination)


async def run_reconcile(
    source: TextIO,
    destination: TextIO,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    transport: httpx.BaseTransport | None = None,
    command: str = "attempt-reconcile",
) -> int:
    """Validate the reference-only request and emit one fixed verdict."""
    try:
        payload = source.read(65537)
        model = SendRequest if command == "attempt-send" else ReconcileRequest
        request = model.model_validate_json(payload if len(payload) <= 65536 else "")
    except (ValueError, TypeError, OSError, RecursionError):
        result = {"verdict": "invalid_input"}
    else:
        result = await execute_admission(command, request, session_factory, transport)
    destination.write(json.dumps(result) + "\n")
    return ADMISSION_EXIT_CODES[result["verdict"]]


def write_signing_request(path: str | None, material: str) -> None:
    """Create an exclusive owner-only artifact without overwriting any existing path."""
    if path is None:
        raise ValueError
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(material + "\n")


def auto_command(arguments: list[str], source: TextIO, destination: TextIO) -> int | None:
    """Route the server-config commands; None leaves the argument list to the older commands."""
    if arguments in (["auto-send"], ["auto-reconcile"]):
        return asyncio.run(run_auto(arguments[0], source, destination))
    if len(arguments) == 3 and arguments[:2] == ["auto-compile", "--output"]:
        return asyncio.run(run_auto_compile(source, destination, arguments[2]))
    if len(arguments) == 3 and arguments[:2] == ["pending", "--limit"]:
        limit = int(arguments[2]) if arguments[2].isdecimal() else 0
        return asyncio.run(run_pending(destination, limit))
    return None


def main(arguments: list[str], source: TextIO, destination: TextIO) -> int:
    """Parse only the explicit offline artifact option without echoing invalid arguments."""
    if not arguments:
        return run(source, destination)
    if arguments in (["attempt-reconcile"], ["attempt-send"]):
        return asyncio.run(run_reconcile(source, destination, command=arguments[0]))
    auto = auto_command(arguments, source, destination)
    if auto is not None:
        return auto
    path = None
    command = arguments[0] if len(arguments) == 1 else ""
    if len(arguments) == 3 and arguments[:2] == ["prepare-message", "--output"]:
        command, path = arguments[0], arguments[2]
    return asyncio.run(run_admission(command, source, destination, output_path=path))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stdout))

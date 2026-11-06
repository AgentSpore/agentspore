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
    ReceiptIntent,
    RenewRequest,
    SignedEnvelope,
)
from app.services.payout_delivery_service import handle_payout_request, verify_receipt
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
    "invalid_input": 5,
    "not_found": 6,
    "conflict": 7,
    "storage_error": 8,
    "busy": 9,
    "stale": 10,
    "unavailable": 11,
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


async def execute_admission(
    command: str,
    intent: AllocationKey | AllocationIntent | ClaimKey,
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> dict:
    """Own transaction completion and dispose only a locally created engine."""
    engine = None
    try:
        if session_factory is None:
            engine = create_async_engine(get_settings().database_url, echo=False)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory.begin() as db:
            repo = PayoutDeliveryRepository(db)
            result = await handle_payout_request(repo, command, intent)
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
        material = result.pop("_signing_request", None)
        if material is not None:
            try:
                write_signing_request(output_path, material)
            except (OSError, ValueError, TypeError):
                result = {"verdict": "invalid_input"}
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


def main(arguments: list[str], source: TextIO, destination: TextIO) -> int:
    """Parse only the explicit offline artifact option without echoing invalid arguments."""
    if not arguments:
        return run(source, destination)
    path = None
    command = arguments[0] if len(arguments) == 1 else ""
    if len(arguments) == 3 and arguments[:2] == ["prepare-message", "--output"]:
        command, path = arguments[0], arguments[2]
    return asyncio.run(run_admission(command, source, destination, output_path=path))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stdout))

"""Verify one explicit payout intent from stdin; emit only a fixed JSON verdict."""

import asyncio
import json
import sys
from typing import TextIO

import httpx
from asyncpg import exceptions as pg_errors
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.repositories.payout_delivery_repo import PayoutDeliveryRepository
from app.schemas.payout_delivery import AllocationIntent, AllocationKey, ReceiptIntent
from app.services.payout_delivery_service import admit_intent, show_intent, verify_receipt

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


async def run_admission(
    command: str,
    source: TextIO,
    destination: TextIO,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Own validation, transaction completion and the lifetime of an owned engine."""
    engine = None
    try:
        payload = source.read(65537)
        model = {"submit": AllocationIntent, "show": AllocationKey}[command]
        intent = model.model_validate_json(payload if len(payload) <= 65536 else "")
    except (KeyError, ValueError, TypeError, OSError, RecursionError):
        result = {"verdict": "invalid_input"}
    else:
        try:
            if session_factory is None:
                engine = create_async_engine(get_settings().database_url, echo=False)
                session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory.begin() as db:
                repo = PayoutDeliveryRepository(db)
                if isinstance(intent, AllocationIntent):
                    result = await admit_intent(repo, intent)
                else:
                    result = await show_intent(repo, intent)
        except ADMISSION_ERRORS:
            # The process boundary must never expose SQL, connection URLs or payloads.
            result = {"verdict": "storage_error"}
        finally:
            if engine is not None:
                try:
                    await engine.dispose()
                except ADMISSION_ERRORS:
                    result = {"verdict": "storage_error"}
    destination.write(json.dumps(result) + "\n")
    return {
        "accepted": 0,
        "replayed": 0,
        "found": 0,
        "invalid_input": 5,
        "not_found": 6,
        "conflict": 7,
        "storage_error": 8,
    }[result["verdict"]]


if __name__ == "__main__":
    sys.exit(
        asyncio.run(run_admission(sys.argv[1] if len(sys.argv) == 2 else "", sys.stdin, sys.stdout))
        if len(sys.argv) > 1
        else run(sys.stdin, sys.stdout)
    )

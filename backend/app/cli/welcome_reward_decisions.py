"""Trusted operator decision writer: bounded stdin, rollback unless --apply."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TextIO

from asyncpg import exceptions as pg_errors
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

STORAGE_ERRORS = (
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
# Model imports initialize database settings; guard that process boundary too.
try:
    from app.core.config import get_settings
    from app.repositories.welcome_reward_repo import WelcomeRewardRepository
    from app.schemas.welcome_reward_decisions import WelcomeRewardDecision
    from app.services.welcome_reward_decision_service import WelcomeRewardDecisionService
except STORAGE_ERRORS:
    IMPORT_READY = False
else:
    IMPORT_READY = True

EXIT_CODES = {
    "validated": 0,
    "recorded": 0,
    "replayed": 0,
    "invalid_input": 5,
    "not_found": 6,
    "conflict": 7,
    "storage_error": 8,
    "stale": 10,
}


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Reject ambiguous duplicate JSON keys before schema validation."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


async def execute(
    intent: WelcomeRewardDecision,
    apply: bool,
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> dict:
    """Report success only after transaction completion and engine disposal."""
    engine = None
    try:
        if not IMPORT_READY:
            raise RuntimeError("storage_unavailable")
        if session_factory is None:
            engine = create_async_engine(
                get_settings().database_url, echo=False, isolation_level="READ COMMITTED"
            )
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            result = await WelcomeRewardDecisionService(WelcomeRewardRepository(db)).record(intent)
            if apply and result.verdict in {"recorded", "replayed"}:
                await db.commit()
            else:
                await db.rollback()
            output = result.model_dump(exclude_none=True) | {
                "applied": apply and result.verdict in {"recorded", "replayed"}
            }
            if not apply and result.verdict == "recorded":
                output = {"verdict": "validated", "applied": False}
    except STORAGE_ERRORS:
        output = {"verdict": "storage_error"}
    finally:
        if engine is not None:
            try:
                await engine.dispose()
            except STORAGE_ERRORS:
                output = {"verdict": "storage_error"}
    return output


async def run(
    source: TextIO,
    destination: TextIO,
    apply: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Validate bounded JSON without exposing payloads or database error details."""
    if not IMPORT_READY:
        destination.write('{"verdict": "storage_error"}\n')
        return 8
    try:
        payload = source.read(8193)
        if len(payload.encode("utf-8")) > 8192:
            raise ValueError("input_too_large")
        json.loads(payload, object_pairs_hook=unique_object)
        intent = WelcomeRewardDecision.model_validate_json(payload)
    except (ValueError, TypeError, OSError, RecursionError):
        output = {"verdict": "invalid_input"}
    else:
        output = await execute(intent, apply, session_factory)
    destination.write(json.dumps(output) + "\n")
    return EXIT_CODES[output["verdict"]]


def main(arguments: list[str], source: TextIO, destination: TextIO) -> int:
    """Accept only the explicit apply flag; never echo invalid arguments."""
    if arguments not in ([], ["--apply"]):
        destination.write('{"verdict": "invalid_input"}\n')
        return 5
    return asyncio.run(run(source, destination, apply=bool(arguments)))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stdout))

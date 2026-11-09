"""Trusted operator decision writer: bounded stdin, rollback unless --apply."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from typing import TextIO
from uuid import UUID

from asyncpg import exceptions as pg_errors
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

STORAGE_ERRORS = (
    SQLAlchemyError,
    pg_errors.PostgresError,
    pg_errors.InterfaceError,
    pg_errors.InternalClientError,
    OSError,
    ValueError,
    RuntimeError,
    OverflowError,
)
LOCK_TIMEOUT_SQLSTATE = "55P03"
# Model imports initialize database settings, and a settings ValidationError echoes env input.
# Only configuration failures (settings, default secret, malformed or unknown database URL)
# are masked; ImportError/TypeError from a code defect propagate as an ordinary traceback
# (code frames and symbol names, no environment values).
try:
    from app.core.config import get_settings
    from app.repositories.welcome_reward_repo import WelcomeRewardRepository
    from app.schemas.welcome_reward_decisions import WelcomeRewardDecision
    from app.services.welcome_beneficiary_service import WelcomeBeneficiaryService
    from app.services.welcome_reward_decision_service import WelcomeRewardDecisionService
except (ValueError, OSError, RuntimeError, ArgumentError):
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
    "configuration_error": 9,
    "stale": 10,
    "busy": 11,
    "ineligible": 12,
    "assessed": 0,
}


def is_lock_timeout(error: BaseException | None) -> bool:
    """Detect lock_timeout through SQLAlchemy's driver wrappers by SQLSTATE."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if getattr(error, "sqlstate", None) == LOCK_TIMEOUT_SQLSTATE:
            return True
        error = getattr(error, "orig", None) or error.__cause__
    return False


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Reject ambiguous duplicate JSON keys before schema validation."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


async def in_session(
    session_factory: async_sessionmaker[AsyncSession] | None,
    work: Callable[[AsyncSession], Awaitable[dict]],
) -> dict:
    """Run work in one session; report only fixed verdicts, dispose the engine we created."""
    engine = None
    try:
        if session_factory is None:
            engine = create_async_engine(
                get_settings().database_url, echo=False, isolation_level="READ COMMITTED"
            )
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            output = await work(db)
    except STORAGE_ERRORS as error:
        output = {"verdict": "busy" if is_lock_timeout(error) else "storage_error"}
    finally:
        if engine is not None:
            try:
                await engine.dispose()
            except STORAGE_ERRORS:
                output = {"verdict": "storage_error"}
    return output


async def execute(
    intent: WelcomeRewardDecision,
    apply: bool,
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> dict:
    """Report success only after transaction completion and engine disposal."""

    async def record(db: AsyncSession) -> dict:
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
        return output

    return await in_session(session_factory, record)


async def run_assess(
    user_id: str,
    destination: TextIO,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Print the canonical beneficiary key and hold codes; writes nothing, never prints email."""
    if not IMPORT_READY:
        output = {"verdict": "configuration_error"}
    else:
        try:
            owner = UUID(user_id)
        except ValueError:
            owner = None
        if owner is None:
            output = {"verdict": "invalid_input"}
        else:

            async def assess(db: AsyncSession) -> dict:
                now = await WelcomeRewardRepository(db).decision_clock()
                found = await WelcomeBeneficiaryService(WelcomeRewardRepository(db)).assess(
                    owner, now
                )
                await db.rollback()
                if found is None:
                    return {"verdict": "not_found"}
                return {"verdict": "assessed"} | found.model_dump(mode="json")

            output = await in_session(session_factory, assess)
    destination.write(json.dumps(output) + "\n")
    return EXIT_CODES[output["verdict"]]


async def run(
    source: TextIO,
    destination: TextIO,
    apply: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Validate bounded JSON without exposing payloads or database error details."""
    if not IMPORT_READY:
        destination.write('{"verdict": "configuration_error"}\n')
        return EXIT_CODES["configuration_error"]
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
    """Accept only the explicit apply flag or `assess --user-id`; never echo invalid arguments."""
    if arguments[:1] == ["assess"]:
        if len(arguments) != 3 or arguments[1] != "--user-id":
            destination.write('{"verdict": "invalid_input"}\n')
            return 5
        return asyncio.run(run_assess(arguments[2], destination))
    if arguments not in ([], ["--apply"]):
        destination.write('{"verdict": "invalid_input"}\n')
        return 5
    return asyncio.run(run(source, destination, apply=bool(arguments)))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stdout))

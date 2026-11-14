"""Trusted operator decision writer: bounded stdin, rollback unless --apply."""

from __future__ import annotations

import asyncio
import functools
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
DEFAULT_MAX = 20
# Model imports initialize database settings, and a settings ValidationError echoes env input.
# Only configuration failures (settings, default secret, malformed or unknown database URL)
# are masked; ImportError/TypeError from a code defect propagate as an ordinary traceback
# (code frames and symbol names, no environment values).
try:
    from app.core.config import Settings, get_settings
    from app.repositories.welcome_auto_decision_repo import WelcomeAutoDecisionRepository
    from app.repositories.welcome_review_repo import WelcomeReviewRepository
    from app.repositories.welcome_reward_repo import WelcomeRewardRepository
    from app.schemas.welcome_reward_decisions import WelcomeRewardDecision
    from app.services.welcome_auto_decision_service import WelcomeAutoDecisionService
    from app.services.welcome_beneficiary_service import WelcomeBeneficiaryService
    from app.services.welcome_review_service import WelcomeReviewService
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
    "auto_decided": 0,
    "disabled": 13,
    "approved_clean": 0,
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


def auto_service(db: AsyncSession, age: int, limit: int) -> WelcomeAutoDecisionService:
    """Bind the automatic rules to one session."""
    return WelcomeAutoDecisionService(
        WelcomeRewardRepository(db), WelcomeAutoDecisionRepository(db), age, limit
    )


async def approve_in_transaction(
    db: AsyncSession, application: dict, rules: tuple[int, int], apply: bool
) -> dict:
    """One approval per short transaction; dry-run rolls back each."""
    outcome = await auto_service(db, *rules).approve(application)
    if apply and outcome == "approved":
        await db.commit()
    else:
        await db.rollback()
    return {"outcome": outcome}


async def shared_engine(
    session_factory: async_sessionmaker[AsyncSession] | None,
    work: Callable[[async_sessionmaker[AsyncSession]], Awaitable[dict]],
    failed: dict,
) -> dict:
    """Run `work` on one engine (ours unless injected), disposed once; else `failed`."""
    engine = None
    output = failed
    try:
        if session_factory is None:
            engine = create_async_engine(
                get_settings().database_url, echo=False, isolation_level="READ COMMITTED"
            )
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
        output = await work(session_factory)
    except STORAGE_ERRORS:
        output = failed
    finally:
        if engine is not None:
            try:
                await engine.dispose()
            except STORAGE_ERRORS:
                output = output | {"verdict": "storage_error"}
    return output


async def auto_decide(
    session_factory: async_sessionmaker[AsyncSession] | None, rules: tuple[int, int], apply: bool
) -> dict:
    """Share one engine across steps; report approvals made before a failure."""
    failed = {"verdict": "storage_error", "approved": 0, "applied": apply}
    return await shared_engine(
        session_factory, lambda factory: approve_all(factory, rules, apply), failed
    )


async def approve_all(
    session_factory: async_sessionmaker[AsyncSession], rules: tuple[int, int], apply: bool
) -> dict:
    """List candidates read-only, then approve them one transaction at a time."""

    async def listing(db: AsyncSession) -> dict:
        found = await auto_service(db, *rules).candidates()
        await db.rollback()
        return {"found": found}

    listed = await in_session(session_factory, listing)
    if "found" not in listed:
        return listed | {"approved": 0, "applied": apply}
    applications, total, remaining = listed["found"]
    approved = 0
    for application in applications:
        if approved >= remaining:
            break
        step = functools.partial(
            approve_in_transaction, application=application, rules=rules, apply=apply
        )
        result = await in_session(session_factory, step)
        if "outcome" not in result:
            return result | {"approved": approved, "applied": apply}
        if result["outcome"] == "capped":
            break
        approved += result["outcome"] == "approved"
    return {
        "verdict": "auto_decided",
        "approved": approved,
        "left": total - approved,
        "applied": apply,
    }


async def run_auto_decide(
    destination: TextIO,
    apply: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> int:
    """Approve rule-satisfying applications; disabled unless both limits are configured."""
    if not IMPORT_READY:
        output = {"verdict": "configuration_error"}
    else:
        settings = settings if settings is not None else get_settings()
        rules = (settings.welcome_auto_min_account_age_days, settings.welcome_auto_daily_limit)
        if min(rules) <= 0:
            output = {"verdict": "disabled"}
        else:
            output = await auto_decide(session_factory, rules, apply)
    destination.write(json.dumps(output) + "\n")
    return EXIT_CODES[output["verdict"]]


def review_service(db: AsyncSession) -> WelcomeReviewService:
    """Bind the review reads and the manual decision writer to one session."""
    return WelcomeReviewService(
        WelcomeRewardRepository(db),
        WelcomeReviewRepository(db),
        WelcomeAutoDecisionRepository(db),
        get_settings().welcome_auto_min_account_age_days,
    )


def age_text(seconds: int) -> str:
    """Compact age: minutes under two hours, hours under two days, else days."""
    if seconds < 7200:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h" if seconds < 172800 else f"{seconds // 86400}d"


def render_review(rows: list, total: int) -> str:
    """Fixed-column table plus a counter line; the address is masked, nothing else identifies."""
    lines = ["GRANT     AGE   EMAIL_DUP  WALLET_DUP  EMAIL                 HOLDS"]
    for row in rows:
        lines.append(
            f"{str(row.grant_id)[:8]}  {age_text(row.age_seconds):<5} "
            f"{'yes' if row.email_match else '-':<10} {'yes' if row.wallet_match else '-':<11} "
            f"{row.email_masked:<21} {','.join(row.holds) or ('expired' if row.expired else '-')}"
        )
    clean = sum(row.clean for row in rows)
    return "\n".join([*lines, f"pending={total} shown={len(rows)} clean={clean}"]) + "\n"


async def read_pending(session_factory: async_sessionmaker[AsyncSession] | None) -> dict:
    """Read-only snapshot: the oldest applications with their holds, and the full count."""

    async def listing(db: AsyncSession) -> dict:
        service = review_service(db)
        found = {"rows": await service.rows(), "total": await service.total()}
        await db.rollback()
        return found

    return await in_session(session_factory, listing)


def finish(output: dict, destination: TextIO) -> int:
    """Write a fixed-verdict JSON line and map it to its exit code."""
    destination.write(json.dumps(output) + "\n")
    return EXIT_CODES[output["verdict"]]


async def run_review(
    destination: TextIO, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> int:
    """Print the pending-application table; writes nothing."""
    if not IMPORT_READY:
        return finish({"verdict": "configuration_error"}, destination)
    found = await read_pending(session_factory)
    if "rows" not in found:
        return finish(found, destination)
    destination.write(render_review(found["rows"], found["total"]))
    return 0


async def run_pending_signal(
    destination: TextIO, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> int:
    """Print the counters-only alert line when applications wait; silent otherwise."""
    if not IMPORT_READY:
        return finish({"verdict": "configuration_error"}, destination)
    found = await read_pending(session_factory)
    if "rows" not in found:
        return finish(found, destination)
    if found["total"] > 0:
        clean = sum(row.clean for row in found["rows"])
        alert = {"alert": "welcome_pending", "pending": found["total"], "clean": clean}
        destination.write(json.dumps(alert) + "\n")
    return 0


async def approve_clean_one(db: AsyncSession, grant_id: UUID, apply: bool) -> dict:
    """One approval per short transaction; a dry run rolls each back."""
    outcome = await review_service(db).approve(grant_id)
    if apply and outcome == "approved":
        await db.commit()
    else:
        await db.rollback()
    return {"outcome": outcome}


async def approve_clean_all(
    session_factory: async_sessionmaker[AsyncSession], apply: bool, limit: int | None
) -> dict:
    """List clean rows, then approve each in its own session; errors report approvals so far."""
    found = await read_pending(session_factory)
    if "rows" not in found:
        return found | {"approved": 0, "applied": apply}
    approved = 0
    for row in [row for row in found["rows"] if row.clean][:limit]:
        step = functools.partial(approve_clean_one, grant_id=row.grant_id, apply=apply)
        result = await in_session(session_factory, step)
        if "outcome" not in result:
            return result | {"approved": approved, "applied": apply}
        approved += result["outcome"] == "approved"
    output = {"verdict": "approved_clean", "approved": approved, "applied": apply}
    return output | {"left": found["total"] - approved}


async def run_approve_clean(
    destination: TextIO,
    apply: bool = False,
    limit: int | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Approve applications without holds or duplicate matches; dry run unless `apply`."""
    if not IMPORT_READY:
        return finish({"verdict": "configuration_error"}, destination)
    failed = {"verdict": "storage_error", "approved": 0, "applied": apply}
    output = await shared_engine(
        session_factory, lambda factory: approve_clean_all(factory, apply, limit), failed
    )
    return finish(output, destination)


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


def main_approve_clean(arguments: list[str], destination: TextIO) -> int:
    """Parse `[--max N] [--apply]`; N must be a positive integer, default DEFAULT_MAX."""
    apply = "--apply" in arguments
    rest = [a for a in arguments if a != "--apply"]
    limit = DEFAULT_MAX
    if rest[:1] == ["--max"] and len(rest) == 2 and rest[1].isdecimal() and int(rest[1]) > 0:
        limit, rest = int(rest[1]), []
    if rest or arguments.count("--apply") > 1:
        return finish({"verdict": "invalid_input"}, destination)
    return asyncio.run(run_approve_clean(destination, apply, limit))


def main_assess(arguments: list[str], destination: TextIO) -> int:
    """Parse `--user-id <uuid>` for `assess`."""
    if len(arguments) != 2 or arguments[0] != "--user-id":
        return finish({"verdict": "invalid_input"}, destination)
    return asyncio.run(run_assess(arguments[1], destination))


def main_auto_decide(arguments: list[str], destination: TextIO) -> int:
    """Parse `[--apply]` for `auto-decide`."""
    if arguments not in ([], ["--apply"]):
        return finish({"verdict": "invalid_input"}, destination)
    return asyncio.run(run_auto_decide(destination, apply=bool(arguments)))


def main_readonly(arguments: list[str], destination: TextIO, runner) -> int:
    """`review` and `pending-signal` take no arguments."""
    if arguments:
        return finish({"verdict": "invalid_input"}, destination)
    return asyncio.run(runner(destination))


def main(arguments: list[str], source: TextIO, destination: TextIO) -> int:
    """Accept `--apply`, `assess --user-id`, `auto-decide [--apply]`, `review`, `pending-signal`
    or `approve-clean [--max N] [--apply]`; never echo others."""
    command, rest = arguments[:1], arguments[1:]
    routes = {
        "assess": lambda: main_assess(rest, destination),
        "auto-decide": lambda: main_auto_decide(rest, destination),
        "approve-clean": lambda: main_approve_clean(rest, destination),
        "review": lambda: main_readonly(rest, destination, run_review),
        "pending-signal": lambda: main_readonly(rest, destination, run_pending_signal),
    }
    if command and command[0] in routes:
        return routes[command[0]]()
    if arguments not in ([], ["--apply"]):
        return finish({"verdict": "invalid_input"}, destination)
    return asyncio.run(run(source, destination, apply=bool(arguments)))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stdout))

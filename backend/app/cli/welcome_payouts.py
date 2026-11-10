"""`welcome-payouts link [--apply]`: bind available welcome grants to payout intents."""

import asyncio
import json
import sys
from typing import TextIO

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services.payout_network_config import ConfigurationError, load_network_config
from app.services.welcome_payout_link_service import WelcomePayoutLinkService

BATCH_LIMIT = 50
EXIT_CODES = {"ok": 0, "invalid_input": 5, "storage_error": 8, "configuration_error": 9}
CONFLICT_EXIT = 7
STORAGE_ERRORS = (SQLAlchemyError, OSError, RuntimeError, ValueError)


async def run_link(
    destination: TextIO,
    apply: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Emit one JSON object of counts and intent ids; failures are fixed verdicts only."""
    engine = None
    try:
        config = load_network_config(get_settings().payout_network_config_path)
        if session_factory is None:
            engine = create_async_engine(
                get_settings().database_url, echo=False, isolation_level="READ COMMITTED"
            )
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
        with httpx.Client(timeout=10, transport=transport, trust_env=False) as client:
            service = WelcomePayoutLinkService(session_factory, config, client)
            output = await service.link(BATCH_LIMIT, apply)
    except ConfigurationError:
        output = {"verdict": "configuration_error"}
    except STORAGE_ERRORS:
        output = {"verdict": "storage_error"}
    finally:
        if engine is not None:
            await engine.dispose()
    destination.write(json.dumps(output) + "\n")
    return CONFLICT_EXIT if output.get("conflict") else EXIT_CODES[output["verdict"]]


def main(arguments: list[str], destination: TextIO) -> int:
    """Accept `link` or `link --apply`; never echo other arguments."""
    if arguments[:1] != ["link"] or arguments[1:] not in ([], ["--apply"]):
        destination.write('{"verdict": "invalid_input"}\n')
        return EXIT_CODES["invalid_input"]
    return asyncio.run(run_link(destination, apply=len(arguments) > 1))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdout))

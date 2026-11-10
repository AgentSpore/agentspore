"""`welcome-payouts link [--apply]`: bind available welcome grants to payout intents."""

import asyncio
import json
import sys
from typing import TextIO

import httpx
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Model imports initialize database settings, and a settings ValidationError echoes env input.
# Only configuration failures are masked; a code defect propagates as an ordinary traceback.
try:
    from app.core.config import get_settings
    from app.services.payout_network_config import ConfigurationError, load_network_config
    from app.services.welcome_payout_link_service import WelcomePayoutLinkService
except (ValueError, OSError, RuntimeError, ArgumentError):
    IMPORT_READY = False
else:
    IMPORT_READY = True

BATCH_LIMIT = 50
EXIT_CODES = {
    "ok": 0,
    "invalid_input": 5,
    "storage_error": 8,
    "configuration_error": 9,
    "rpc_error": 10,
}
CONFLICT_EXIT = 7
CONFIG_ERRORS = (ValueError, OSError, RuntimeError, ArgumentError)
STORAGE_ERRORS = (SQLAlchemyError, OSError, RuntimeError, ValueError)


async def run_link(
    destination: TextIO,
    apply: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Emit one JSON object of counts and intent ids; failures are fixed verdicts only."""
    engine = None
    if not IMPORT_READY:
        output = {"verdict": "configuration_error"}
    else:
        try:
            settings = get_settings()
            config = load_network_config(settings.payout_network_config_path)
        except (ConfigurationError, *CONFIG_ERRORS):
            output = {"verdict": "configuration_error"}
        else:
            try:
                if session_factory is None:
                    engine = create_async_engine(
                        settings.database_url, echo=False, isolation_level="READ COMMITTED"
                    )
                    session_factory = async_sessionmaker(engine, expire_on_commit=False)
                with httpx.Client(timeout=10, transport=transport, trust_env=False) as client:
                    service = WelcomePayoutLinkService(session_factory, config, client)
                    output = await service.link(BATCH_LIMIT, apply)
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

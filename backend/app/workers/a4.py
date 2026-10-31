"""Entrypoint of the `a4-worker` service: `python -m app.workers.a4`."""

import asyncio
import signal

from app.core.background import DeploymentWorkerTask, ScheduledTask
from app.core.config import get_settings
from app.core.database import async_session_maker
from app.core.logging import setup_logging
from app.services.deployment.buildkit import builder_from_settings
from app.services.deployment.scans import DockerfileScanner, GitleaksScanner, OsvScanner
from app.services.deployment.worker import DeploymentWorker


def build_task() -> DeploymentWorkerTask:
    """Wire the worker; `A4_BUILDER` picks the builder. P3 supplies the base-image allowlist."""
    scanners = [GitleaksScanner(), OsvScanner(), DockerfileScanner(allowed_digests=frozenset())]
    return DeploymentWorkerTask(
        DeploymentWorker(async_session_maker, builder_from_settings(get_settings()), scanners)
    )


async def serve(task: ScheduledTask) -> None:
    """Run the loop until SIGTERM/SIGINT, which cancel it so the running build is closed out."""
    loop = asyncio.get_running_loop()
    running = asyncio.create_task(task.start())
    signals = (signal.SIGTERM, signal.SIGINT)
    for sig in signals:
        loop.add_signal_handler(sig, running.cancel)
    try:
        await running
    except asyncio.CancelledError:
        if not running.cancelled():
            raise
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)


def main() -> None:
    setup_logging()
    asyncio.run(serve(build_task()))


if __name__ == "__main__":
    main()

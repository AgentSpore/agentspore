"""Entrypoint of the `a4-worker` service: `python -m app.workers.a4`."""

import asyncio
import signal

from app.core.background import DeploymentWorkerTask, ScheduledTask
from app.core.config import get_settings
from app.core.database import async_session_maker
from app.core.logging import setup_logging
from app.services.deployment.buildkit import builder_from_settings
from app.services.deployment.launcher_client import LauncherClient
from app.services.deployment.launcher_runtime import LauncherRuntime
from app.services.deployment.scans import DockerfileScanner, GitleaksScanner, OsvScanner, Scanner
from app.services.deployment.worker import DeploymentWorker
from app.services.deployment_health import DeploymentHealthProbe
from app.services.deployment_promotion import DeploymentPromotionService


def build_task() -> DeploymentWorkerTask:
    """Wire the worker; `A4_BUILDER` picks the builder. P3 supplies the base-image allowlist."""
    scanners: list[Scanner] = [
        GitleaksScanner(),
        OsvScanner(),
        DockerfileScanner(allowed_digests=frozenset()),
    ]
    settings = get_settings()
    worker = DeploymentWorker(async_session_maker, builder_from_settings(settings), scanners)
    if settings.a4_runtime == "launcher":
        client = LauncherClient(settings.a4_launcher_url)
        promotion = DeploymentPromotionService(async_session_maker, DeploymentHealthProbe())
        public_url = f"https://{settings.domains[0]}/"
        worker.attach_runtime(LauncherRuntime(async_session_maker, client, promotion, public_url))
    return DeploymentWorkerTask(worker)


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

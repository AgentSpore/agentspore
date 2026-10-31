"""Entrypoint of the `a4-worker` service: `python -m app.workers.a4`."""

import asyncio

from app.core.background import DeploymentWorkerTask
from app.core.database import async_session_maker
from app.services.deployment.builder import FakeBuilder
from app.services.deployment.scans import DockerfileScanner, GitleaksScanner, OsvScanner
from app.services.deployment.worker import DeploymentWorker


def build_task() -> DeploymentWorkerTask:
    """Wire P2 parts; P3 swaps `FakeBuilder` and supplies the real base-image allowlist."""
    scanners = [GitleaksScanner(), OsvScanner(), DockerfileScanner(allowed_digests=frozenset())]
    return DeploymentWorkerTask(DeploymentWorker(async_session_maker, FakeBuilder(), scanners))


if __name__ == "__main__":
    asyncio.run(build_task().start())

"""Recreate the BuildKit daemon after every build ("one build per daemon").

A `RUN` step can outlive or ptrace the daemon, so nothing from one build may reach the next.
The worker never holds the Docker socket: it asks `a4-restart-gate` (see
`deploy/a4-builder/restart-gate.conf`), which allows only restart and inspect of the buildkitd
container. The daemon's state is a tmpfs, so a restart wipes it.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from loguru import logger

from app.services.deployment.builder import Builder, BuildRequest, BuildResult
from app.services.deployment.errors import DeployRejectedError

CANCEL_RECYCLE_TIMEOUT = 20.0  # a4-worker stop_grace_period is 30 s


class Recycler(Protocol):
    async def ensure_clean(self) -> None:
        """Return only when the daemon is known to be fresh; else raise `DeployRejectedError`."""

    async def recycle(self) -> None:
        """Restart the daemon after a build; never raises (a failure leaves the daemon dirty)."""


@dataclass
class DaemonRecycler:
    """Fail closed: dirty until a restart is confirmed running and healthy."""

    gate_url: str
    container: str = "a4-builder-buildkitd-1"
    api_version: str = "v1.44"
    stop_timeout: int = 5
    ready_timeout: float = 90.0
    poll_interval: float = 1.0
    transport: httpx.AsyncBaseTransport | None = None
    dirty: bool = field(
        default=True, init=False
    )  # unknown state at start: recycle before the first build

    async def ensure_clean(self) -> None:
        if self.dirty:
            await self.recycle()
        if self.dirty:
            raise DeployRejectedError("tool_failed", "buildkitd recycle failed, builds refused")

    async def recycle(self) -> None:
        self.dirty = True
        try:
            async with httpx.AsyncClient(
                base_url=self.gate_url, transport=self.transport, timeout=30.0
            ) as client:
                await self._restart(client)
                await self._wait_ready(client)
        except (
            httpx.HTTPError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
        ) as exc:
            logger.error("buildkitd recycle failed container={} error={!r}", self.container, exc)
            return
        self.dirty = False

    def _path(self, action: str) -> str:
        return f"/{self.api_version}/containers/{self.container}/{action}"

    async def _restart(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(self._path("restart"), params={"t": self.stop_timeout})
        resp.raise_for_status()

    async def _wait_ready(self, client: httpx.AsyncClient) -> None:
        deadline = time.monotonic() + self.ready_timeout
        while True:
            resp = await client.get(self._path("json"))
            resp.raise_for_status()
            state = resp.json()["State"]
            if state["Running"] and state.get("Health", {}).get("Status") == "healthy":
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"not healthy after {self.ready_timeout:g}s")
            await asyncio.sleep(self.poll_interval)


@dataclass
class RecyclingBuilder:
    """Wraps a `Builder`: fresh daemon before each build, restart after it, whatever the outcome.

    A failed restart leaves the recycler dirty, so the next `build` is refused (fail closed).
    """

    inner: Builder
    recycler: Recycler

    async def build(self, request: BuildRequest) -> BuildResult:
        await self.recycler.ensure_clean()
        try:
            return await self.inner.build(request)
        finally:  # success, failure, timeout and cancel all end with a restart
            await self._recycle_within_shutdown_budget()

    async def _recycle_within_shutdown_budget(self) -> None:
        """While cancelling (SIGTERM, lost lease) the restart gets `CANCEL_RECYCLE_TIMEOUT`, so the
        worker still writes its outcome inside `stop_grace_period`; the daemon then stays dirty."""
        task = asyncio.current_task()
        if task is None or not task.cancelling():
            await self.recycler.recycle()
            return
        try:
            async with asyncio.timeout(CANCEL_RECYCLE_TIMEOUT):
                await self.recycler.recycle()
        except TimeoutError:
            logger.error("buildkitd recycle cut short while cancelling, daemon stays dirty")

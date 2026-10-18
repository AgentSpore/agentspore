"""Observe and stop only trusted build containers through an explicit Unix socket."""

import asyncio
import json
import re
from pathlib import Path

import httpx

from app.schemas.deployments import (
    DeploymentLease,
    SandboxNamespaceAbsenceProof,
    SandboxStopProof,
)


class DeploymentBuildCleanup:
    """Own direct Docker HTTP transport; caller separately commits observed proofs."""

    def __init__(self, socket_path: str, transport: httpx.AsyncBaseTransport | None = None):
        if not Path(socket_path).is_absolute() or "\0" in socket_path:
            raise ValueError("absolute_socket_path_required")
        self.transport = transport or httpx.AsyncHTTPTransport(uds=socket_path, trust_env=False)

    async def _exchange(self, request: httpx.Request) -> tuple[int, bytes] | None:
        response = None
        result = None
        try:
            async with asyncio.timeout(1.8):
                response = await self.transport.handle_async_request(request)
                body = b""
                if request.method == "GET":
                    if response.is_stream_consumed:
                        body = response.content
                    else:
                        async for chunk in response.aiter_raw():
                            if len(body) + len(chunk) > 65536:
                                raise ValueError("inventory_too_large")
                            body += chunk
                    if len(body) > 65536:
                        raise ValueError("inventory_too_large")
                result = response.status_code, body
        except (TimeoutError, OSError, httpx.HTTPError, ValueError):
            result = None
        finally:
            if response is not None:
                try:
                    async with asyncio.timeout(0.2):
                        await response.aclose()
                except (TimeoutError, OSError, httpx.HTTPError):
                    result = None
        return result

    async def _inventory(self, filters: dict[str, list[str]]) -> list[dict] | None:
        url = httpx.URL(
            "http://docker/containers/json", params={"all": "true", "filters": json.dumps(filters)}
        )
        result = await self._exchange(httpx.Request("GET", url))
        if result is None or result[0] != 200:
            return None
        try:
            rows = json.loads(result[1])
        except (ValueError, RecursionError):
            return None
        return (
            rows if isinstance(rows, list) and all(isinstance(row, dict) for row in rows) else None
        )

    @staticmethod
    def _owned(rows: list[dict] | None, lease: DeploymentLease) -> bool:
        if rows is None or len(rows) > 1:
            return False
        labels = {
            "namespace": lease.sandbox_namespace,
            "deployment": str(lease.deployment_id),
            "worker": str(lease.worker_id),
            "generation": str(lease.generation),
            "role": "build",
        }
        return all(
            isinstance(row.get("Id"), str)
            and re.fullmatch(r"[0-9a-f]{64}", row["Id"])
            and isinstance(row.get("State"), str)
            and row["State"] in {"running", "exited", "created", "dead"}
            and isinstance(row.get("Labels"), dict)
            and all(
                row["Labels"].get("com.agentspore.a4." + key) == value
                for key, value in labels.items()
            )
            for row in rows
        )

    async def _pair(
        self, lease: DeploymentLease, identity: str
    ) -> tuple[list[dict], list[dict]] | None:
        by_id = await self._inventory({"id": [identity]})
        namespace = await self._inventory(
            {"label": ["com.agentspore.a4.namespace=" + lease.sandbox_namespace]}
        )
        if not self._owned(by_id, lease) or not self._owned(namespace, lease):
            return None
        if by_id is None or namespace is None:
            return None
        if {row["Id"] for row in by_id} != {row["Id"] for row in namespace}:
            return None
        if by_id and by_id[0]["Id"] != identity:
            return None
        return by_id, namespace

    async def _stopped(self, identity: str) -> bool:
        """Read the post-stop state from inspect, never from the container list.

        moby `handleContainerExit` wakes `stop` waiters in `SetStopped` before
        `CheckpointTo(containersReplica)`, and `/containers/json` reads that replica,
        so right after a 204 the list can still say `running`. Inspect takes the
        container lock and is consistent.
        """
        result = await self._exchange(
            httpx.Request("GET", f"http://docker/containers/{identity}/json")
        )
        if result is None or result[0] != 200:
            return False
        try:
            body = json.loads(result[1])
        except (ValueError, RecursionError):
            return False
        state = body.get("Id") == identity and body.get("State") if isinstance(body, dict) else None
        return isinstance(state, dict) and state.get("Status") in {"exited", "created", "dead"}

    async def observe_stopped(
        self,
        lease: DeploymentLease,
        recorded_id: str | None,
    ) -> SandboxStopProof | SandboxNamespaceAbsenceProof | None:
        """Return observations, never infer host-wide absence or database authorization."""
        if recorded_id is not None and re.fullmatch(r"[0-9a-f]{64}", recorded_id) is None:
            return None
        try:
            async with asyncio.timeout(9.8):
                return await self._observe(lease, recorded_id)
        except TimeoutError:
            return None

    async def _observe(self, lease: DeploymentLease, identity: str | None):
        if identity is None:
            rows = await self._inventory(
                {"label": ["com.agentspore.a4.namespace=" + lease.sandbox_namespace]}
            )
            if not self._owned(rows, lease) or rows is None:
                return None
            if not rows:
                return SandboxNamespaceAbsenceProof(lease=lease, namespace=lease.sandbox_namespace)
            identity = rows[0]["Id"]
        before = await self._pair(lease, identity)
        if before is None:
            return None
        if before[0]:
            url = httpx.URL(f"http://docker/containers/{identity}/stop", params={"t": "1"})
            stopped = await self._exchange(httpx.Request("POST", url))
            if stopped is None or stopped[0] not in {204, 304}:
                return None
            if await self._pair(lease, identity) is None or not await self._stopped(identity):
                return None
        return SandboxStopProof(lease=lease, sandbox_id=identity)

    async def aclose(self) -> None:
        """Bound transport shutdown while preserving caller cancellation."""
        try:
            async with asyncio.timeout(0.2):
                await self.transport.aclose()
        except (TimeoutError, OSError, httpx.HTTPError):
            return

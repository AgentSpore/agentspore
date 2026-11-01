"""Client of the `a4-launcher` control API. Launcher text never reaches a tenant-visible field."""

import asyncio
import json
from typing import Any

import httpx
from loguru import logger

from app.services.deployment.errors import DeployRejectedError

MAX_REPLY_BYTES = 64 * 1024
_REPLY_TOO_LARGE = "launcher reply is too large"
# The operation deadline bounds reads, including image pulls and graceful stops.
_CONNECT = httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0)
_PUT_SECONDS = 180.0  # includes the image pull
_STOP_SECONDS = 45.0
_QUICK_SECONDS = 15.0
_PASS_THROUGH = frozenset(
    {"image_not_allowed", "digest_mismatch", "platform_not_allowed", "image_volume_not_allowed"}
)
_BY_STATUS = {409: "runtime_conflict", 429: "runtime_capacity", 503: "runtime_capacity"}
UNAVAILABLE = "runtime_unavailable"


def rejection(status: int, reason: str | None) -> DeployRejectedError:
    """Map a launcher refusal to the stable vocabulary; only the reason code is consulted."""
    if status == 422 and reason in _PASS_THROUGH:
        return DeployRejectedError(reason)
    return DeployRejectedError(_BY_STATUS.get(status, UNAVAILABLE))


class LauncherClient:
    """Typed calls over the internal `a4-control` network; every call has a total deadline."""

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=_CONNECT,
            follow_redirects=False,
            trust_env=False,
        )

    async def put(self, ns: str, body: dict[str, Any]) -> str:
        """Create network and container without starting; return the container id."""
        data = await self._call("PUT", f"/v1/runtimes/{ns}", _PUT_SECONDS, body)
        container_id = data.get("container_id")
        if not isinstance(container_id, str) or not container_id:
            raise DeployRejectedError(UNAVAILABLE)
        return container_id

    async def start(self, ns: str) -> None:
        await self._call("POST", f"/v1/runtimes/{ns}/start", _QUICK_SECONDS)

    async def stop(self, ns: str) -> None:
        await self._call("POST", f"/v1/runtimes/{ns}/stop", _STOP_SECONDS)

    async def delete(self, ns: str) -> None:
        await self._call("DELETE", f"/v1/runtimes/{ns}", _STOP_SECONDS)

    async def list_runtimes(self) -> list[dict[str, str]]:
        data = await self._call("GET", "/v1/runtimes", _QUICK_SECONDS)
        runtimes = data.get("runtimes")
        if not isinstance(runtimes, list) or not all(isinstance(r, dict) for r in runtimes):
            raise DeployRejectedError(UNAVAILABLE)
        return runtimes

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(
        self, method: str, path: str, seconds: float, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            async with asyncio.timeout(seconds), self._http.stream(method, path, json=body) as resp:
                raw = await _read_capped(resp)
                status = resp.status_code
        except (TimeoutError, httpx.HTTPError) as exc:
            logger.warning("launcher {} {} failed: {}", method, path, type(exc).__name__)
            raise DeployRejectedError(UNAVAILABLE) from exc
        data = _json_object(raw)
        if 200 <= status < 300:
            if method in {"POST", "DELETE"} and data.get("ok") is not True:
                raise DeployRejectedError(UNAVAILABLE)
            return data
        reason = data.get("reason")
        logger.warning(
            "launcher {} {} answered {} reason={} docker_status={}",
            method,
            path,
            status,
            reason if isinstance(reason, str) else None,
            data.get("docker_status"),
        )
        raise rejection(status, reason if isinstance(reason, str) else None)


async def _read_capped(resp: httpx.Response) -> bytes:
    raw = b""
    async for part in resp.aiter_bytes():
        raw += part
        if len(raw) > MAX_REPLY_BYTES:
            raise httpx.TransportError(_REPLY_TOO_LARGE)
    return raw


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}

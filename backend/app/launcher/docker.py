"""Docker Engine API over the unix socket: timeouts and a size cap on every call."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.services.deployment.process import redact

SOCKET = "/var/run/docker.sock"
API = "http://docker/v1.44"
MAX_REPLY = 4 * 1024 * 1024


def query(path: str, **params: str) -> str:
    return f"{path}?{urlencode(params)}"


class DockerError(Exception):
    def __init__(self, status: int, text: str) -> None:
        super().__init__(status)
        self.status, self.text = status, text


@dataclass(frozen=True)
class Reply:
    status: int
    data: bytes

    def json(self) -> Any:
        return json.loads(self.data)


class DockerClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(
            transport=transport or httpx.AsyncHTTPTransport(uds=SOCKET),
            base_url=API,
            timeout=httpx.Timeout(10.0),
        )

    async def call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        ok: tuple[int, ...] = (200, 201, 204),
        timeout: float = 15.0,
    ) -> Reply:
        """Raise DockerError (redacted, truncated daemon text) unless the status is in `ok`."""
        try:
            async with asyncio.timeout(timeout):
                reply = await self._send(method, path, body)
        except (TimeoutError, httpx.HTTPError) as exc:
            raise DockerError(0, type(exc).__name__) from exc
        if reply.status not in ok:
            text = redact(reply.data[:500]).decode("utf-8", "replace")
            raise DockerError(reply.status, text)
        return reply

    async def _send(self, method: str, path: str, body: Any) -> Reply:
        request = self._http.build_request(method, path, json=body)
        response = await self._http.send(request, stream=True)
        chunks, size = [], 0
        try:
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_REPLY:
                    raise DockerError(0, "response_too_large")
                chunks.append(chunk)
        finally:
            await response.aclose()
        return Reply(response.status_code, b"".join(chunks))

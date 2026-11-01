"""Push the verified image tarball to the private `a4-registry` over the OCI distribution API.

No `docker load`. The registry is unauthenticated (loopback/internal network), so no credential can
leak into a URL or log. Redirects are never followed; an upload `Location` must keep the registry
origin. The returned reference is digest-only, never a tag.
"""

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.services.deployment.builder import Builder, BuildRequest, BuildResult
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.image_plan import Blob, ImagePlan, plan_image
from app.services.deployment.process import redact

MAX_REPLY_BYTES = 16 * 1024
_CHUNK = 1024 * 1024
_REPO_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)
_TOTAL_SECONDS = 900.0


def _failed(detail: str) -> DeployRejectedError:
    return DeployRejectedError("tool_failed", detail)


@dataclass(frozen=True)
class _Reply:
    status: int
    headers: httpx.Headers
    body: bytes

    def expect(self, *ok: int) -> None:
        if self.status not in ok:
            text = redact(self.body[:512]).decode(errors="replace").strip()
            raise _failed(f"registry answered {self.status}: {text}".rstrip(": "))


class RegistryPusher:
    def __init__(
        self,
        base_url: str,
        ref_host: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base = httpx.URL(base_url)
        self.ref_host = ref_host
        self.transport = transport

    async def push(self, tar: Path, repo: str) -> str:
        """Upload the image in `tar` as `repo`; return the digest-only reference."""
        if not _REPO_RE.fullmatch(repo) or len(repo) > 128:
            raise _failed("registry repository name is not allowed")
        plan = await asyncio.to_thread(plan_image, tar)
        try:
            async with asyncio.timeout(_TOTAL_SECONDS), self._client() as client:
                for blob in plan.blobs:
                    await self._put_blob(client, tar, repo, blob)
                await self._put_manifest(client, repo, plan)
        except (httpx.HTTPError, TimeoutError) as exc:
            raise _failed(f"registry unreachable: {type(exc).__name__}") from exc
        return f"{self.ref_host}/{repo}@{plan.manifest.digest}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base,
            transport=self.transport,
            timeout=_TIMEOUT,
            follow_redirects=False,
            trust_env=False,
        )

    @staticmethod
    async def _send(client: httpx.AsyncClient, method: str, url: str | httpx.URL, **kw) -> _Reply:
        async with client.stream(method, url, **kw) as resp:
            if resp.is_redirect or 300 <= resp.status_code < 400:
                raise _failed("registry answered with a redirect")
            body = b""
            async for part in resp.aiter_bytes():
                body += part
                if len(body) > MAX_REPLY_BYTES:
                    raise _failed("registry reply is too large")
            return _Reply(resp.status_code, resp.headers, body)

    async def _put_blob(self, client: httpx.AsyncClient, tar: Path, repo: str, blob: Blob) -> None:
        head = await self._send(client, "HEAD", f"/v2/{repo}/blobs/{blob.digest}")
        if head.status == 200 and head.headers.get("content-length") == str(blob.size):
            return
        start = await self._send(client, "POST", f"/v2/{repo}/blobs/uploads/")
        start.expect(202)
        upload_url = self.base.join(f"/v2/{repo}/blobs/uploads/")
        target = self._same_origin(start.headers.get("location"), upload_url)
        reply = await self._send(
            client,
            "PUT",
            target.copy_add_param("digest", blob.digest),
            content=_blob_stream(tar, blob),
            headers={
                "Content-Length": str(blob.size),
                "Content-Type": "application/octet-stream",
            },
        )
        reply.expect(201)

    async def _put_manifest(self, client: httpx.AsyncClient, repo: str, plan: ImagePlan) -> None:
        reply = await self._send(
            client,
            "PUT",
            f"/v2/{repo}/manifests/{plan.manifest.digest}",
            content=plan.body,
            headers={"Content-Type": plan.media_type},
        )
        reply.expect(201)
        echoed = reply.headers.get("docker-content-digest")
        if echoed is not None and echoed != plan.manifest.digest:
            raise _failed("registry stored a different manifest digest")

    def _same_origin(self, location: str | None, upload_url: httpx.URL) -> httpx.URL:
        """`location` resolved against the upload URL must keep the registry's exact origin."""
        try:
            target = upload_url.join(location or "")
        except httpx.InvalidURL as exc:
            raise _failed("registry upload location is invalid") from exc
        origin = (target.scheme, target.host, target.port)
        if (
            not location
            or target.userinfo
            or origin != (self.base.scheme, self.base.host, self.base.port)
        ):
            raise _failed("registry upload location leaves the registry origin")
        return target


async def _blob_stream(tar: Path, blob: Blob) -> AsyncIterator[bytes]:
    with tar.open("rb") as raw:
        raw.seek(blob.offset)
        left = blob.size
        while left:
            chunk = await asyncio.to_thread(raw.read, min(_CHUNK, left))
            if not chunk:
                raise _failed("image tar blob is truncated")
            left -= len(chunk)
            yield chunk


@dataclass
class PushingBuilder:
    """Replace the built tarball by a registry reference; the tarball is deleted either way."""

    inner: Builder
    pusher: RegistryPusher

    async def build(self, request: BuildRequest) -> BuildResult:
        result = await self.inner.build(request)
        tar = result.artifact
        try:
            if tar is None:
                raise _failed("builder reported no image tarball")
            ref = await self.pusher.push(tar, request.tag.partition(":")[0])
        finally:
            if tar is not None:
                with contextlib.suppress(OSError):
                    tar.unlink(missing_ok=True)
        if ref.rpartition("@")[2] != result.image_ref.rpartition("@")[2]:
            raise _failed("pushed digest differs from the build digest")
        return BuildResult(image_ref=ref, image_bytes=result.image_bytes)

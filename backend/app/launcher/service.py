"""Launcher operations. Every Docker object is owned by label; foreign objects are never touched."""

import asyncio
import ipaddress
import json
from typing import Any

from app.launcher.docker import DockerClient, query
from app.launcher.schemas import IMAGE_RE, LauncherError, RuntimeRequest
from app.launcher.spec import LP, RUNTIME, container_body, network_body

SUBNET_PREFIX = 28
ARCH = "amd64"


class Launcher:
    def __init__(
        self,
        docker: DockerClient,
        attach: tuple[str, ...],
        pool: ipaddress.IPv4Network,
        runtime: str = RUNTIME,
        arch: str = ARCH,
    ) -> None:
        self._d, self._attach, self._pool = docker, attach, pool
        self._runtime, self._arch = runtime, arch
        self._net_lock = asyncio.Lock()

    async def put(self, ns: str, req: RuntimeRequest) -> str:
        m = IMAGE_RE.fullmatch(req.image)
        if not m or m.group(1) != ns:
            raise LauncherError(422, "image_not_allowed")
        await self._pull_and_verify(req.image)
        await self._ensure_network(ns, req)
        return await self._create(ns, req)

    async def _pull_and_verify(self, ref: str) -> None:
        repo, digest = ref.split("@")
        pull = await self._d.call(
            "POST", query("/images/create", fromImage=repo, tag=digest), timeout=300.0
        )
        if b'"error"' in pull.data:
            raise LauncherError(502, "pull_failed")
        info = (await self._d.call("GET", f"/images/{ref}/json")).json()
        if ref not in (info.get("RepoDigests") or []):
            raise LauncherError(422, "digest_mismatch")
        if info.get("Os") != "linux" or info.get("Architecture") != self._arch:
            raise LauncherError(422, "platform_not_allowed")
        if (info.get("Config") or {}).get("Volumes"):
            raise LauncherError(422, "image_volume_not_allowed")

    async def _ensure_network(self, ns: str, req: RuntimeRequest) -> None:
        async with self._net_lock:
            reply = await self._d.call("GET", f"/networks/{ns}", ok=(200, 404))
            if reply.status == 200:
                self._require_owned(reply.json().get("Labels"), ns)
            else:
                subnet = await self._free_subnet()
                await self._d.call("POST", "/networks/create", body=network_body(req, str(subnet)))
        connected = (await self._d.call("GET", f"/networks/{ns}")).json().get("Containers") or {}
        names = {c.get("Name") for c in connected.values()}
        for name in self._attach:
            if name not in names:
                await self._d.call("POST", f"/networks/{ns}/connect", body={"Container": name})

    async def _free_subnet(self) -> ipaddress.IPv4Network:
        nets = (await self._d.call("GET", "/networks")).json()
        used = []
        for net in nets:
            for cfg in (net.get("IPAM") or {}).get("Config") or []:
                parsed = ipaddress.ip_network(cfg.get("Subnet", "0.0.0.0/32"), strict=False)
                if parsed.version == 4:
                    used.append(parsed)
        for cand in self._pool.subnets(new_prefix=SUBNET_PREFIX):
            if not any(cand.overlaps(u) for u in used):
                return cand
        raise LauncherError(503, "pool_exhausted")

    async def _create(self, ns: str, req: RuntimeRequest) -> str:
        body = container_body(req, self._runtime)
        reply = await self._d.call(
            "POST", query("/containers/create", name=ns), body=body, ok=(201, 409)
        )
        if reply.status == 201:
            return reply.json()["Id"]
        info = (await self._d.call("GET", f"/containers/{ns}/json")).json()
        cfg = info.get("Config") or {}
        have = cfg.get("Labels") or {}
        if cfg.get("Image") != req.image or any(
            have.get(k) != v for k, v in body["Labels"].items()
        ):
            raise LauncherError(409, "namespace_owned")
        return info["Id"]

    async def start(self, ns: str) -> None:
        await self._container(ns, required=True)
        await self._d.call("POST", f"/containers/{ns}/start", ok=(204, 304))

    async def stop(self, ns: str) -> None:
        if await self._container(ns, required=False):
            await self._d.call(
                "POST",
                query(f"/containers/{ns}/stop", t="10"),
                ok=(204, 304, 404),
                timeout=30.0,
            )

    async def delete(self, ns: str) -> None:
        await self.stop(ns)
        if await self._container(ns, required=False):
            await self._d.call(
                "DELETE", query(f"/containers/{ns}", force="true", v="true"), ok=(204, 404)
            )
        reply = await self._d.call("GET", f"/networks/{ns}", ok=(200, 404))
        if reply.status == 404:
            return
        net = reply.json()
        self._require_owned(net.get("Labels"), ns)
        for c in (net.get("Containers") or {}).values():
            await self._d.call(
                "POST",
                f"/networks/{ns}/disconnect",
                body={"Container": c["Name"], "Force": True},
                ok=(200, 404),
            )
        await self._d.call("DELETE", f"/networks/{ns}", ok=(204, 404))

    async def _container(self, ns: str, *, required: bool) -> dict[str, Any] | None:
        reply = await self._d.call("GET", f"/containers/{ns}/json", ok=(200, 404))
        if reply.status == 404:
            if required:
                raise LauncherError(404, "not_found")
            return None
        info = reply.json()
        self._require_owned((info.get("Config") or {}).get("Labels"), ns)
        return info

    @staticmethod
    def _require_owned(have: dict[str, str] | None, ns: str) -> None:
        have = have or {}
        if have.get(LP + "launcher") != "1" or have.get(LP + "namespace") != ns:
            raise LauncherError(409, "namespace_owned")

    async def list_runtimes(self) -> list[dict[str, str]]:
        flt = json.dumps({"label": [LP + "role=runtime", LP + "launcher=1"]})
        reply = await self._d.call("GET", query("/containers/json", all="true", filters=flt))
        out = []
        for c in reply.json():
            lb = c.get("Labels") or {}
            out.append(
                {
                    "namespace": lb.get(LP + "namespace", ""),
                    "container_id": c.get("Id", ""),
                    "state": c.get("State", ""),
                    "deployment": lb.get(LP + "deployment", ""),
                    "worker": lb.get(LP + "worker", ""),
                    "generation": lb.get(LP + "generation", ""),
                }
            )
        return out

    async def gc_images(self, keep: list[str]) -> list[str]:
        removed = []
        for img in (await self._d.call("GET", "/images/json")).json():
            for ref in img.get("RepoDigests") or []:
                if IMAGE_RE.fullmatch(ref) and ref not in keep:
                    reply = await self._d.call("DELETE", f"/images/{ref}", ok=(200, 404, 409))
                    if reply.status == 200:
                        removed.append(ref)
        return removed

"""`python -m app.launcher`: configuration comes from A4_ATTACH and A4_TENANT_POOL only."""

import ipaddress
import os
import re

import uvicorn

from app.launcher.app import build_app
from app.launcher.docker import DockerClient
from app.launcher.service import SUBNET_PREFIX, Launcher

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def configure() -> Launcher:
    attach = tuple(n for n in os.environ.get("A4_ATTACH", "").split(",") if n)
    if not all(NAME_RE.fullmatch(n) for n in attach):
        raise SystemExit("A4_ATTACH: invalid container name")
    pool = ipaddress.ip_network(os.environ.get("A4_TENANT_POOL", "10.214.0.0/16"))
    if not isinstance(pool, ipaddress.IPv4Network) or pool.prefixlen > SUBNET_PREFIX:
        raise SystemExit("A4_TENANT_POOL: need an IPv4 network of /28 or larger")
    return Launcher(DockerClient(), attach, pool)


if __name__ == "__main__":
    uvicorn.run(build_app(configure()), host="0.0.0.0", port=8080, access_log=False)  # noqa: S104 (binds inside the container; reachable only on the a4-control network)

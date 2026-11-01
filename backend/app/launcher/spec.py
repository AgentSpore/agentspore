"""Docker objects built only from constants and validated request fields."""

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.launcher.schemas import RuntimeRequest

LP = "com.agentspore.a4."
RUNTIME = "runsc"
ARCH = "amd64"
USER = "10001:10001"
MEMORY = 256 * 1024 * 1024
PORT = "8080"


@dataclass(frozen=True)
class Target:
    """OCI runtime and image architecture; tests override them, production never does."""

    runtime: str = RUNTIME
    arch: str = ARCH


def alias(req: RuntimeRequest) -> str:
    return f"svc-{req.deployment_id}"


def labels(req: RuntimeRequest, role: str) -> dict[str, str]:
    """Identity labels; `spec` fingerprints env and URL, so a replay with other values is a 409."""
    spec = json.dumps([req.env, req.public_url], sort_keys=True).encode()
    return {
        LP + "launcher": "1",
        LP + "role": role,
        LP + "namespace": req.namespace,
        LP + "deployment": req.deployment_id,
        LP + "worker": req.worker_id,
        LP + "generation": str(req.generation),
        LP + "spec": sha256(spec).hexdigest(),
    }


def container_body(req: RuntimeRequest, runtime: str = RUNTIME) -> dict[str, Any]:
    ns = req.namespace
    env = {**req.env, "PORT": PORT, "PUBLIC_URL": req.public_url, "HOSTNAME": alias(req)}
    return {
        "User": USER,
        "Image": req.image,
        "Env": [f"{k}={v}" for k, v in env.items()],
        "Labels": labels(req, "runtime"),
        "Healthcheck": {"Test": ["NONE"]},
        "NetworkingConfig": {"EndpointsConfig": {ns: {"Aliases": [alias(req)]}}},
        "HostConfig": {
            "Runtime": runtime,
            "Privileged": False,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "NanoCpus": 500_000_000,
            "Memory": MEMORY,
            "MemorySwap": MEMORY,
            "PidsLimit": 64,
            "Tmpfs": {"/tmp": "size=32m,noexec,nosuid,nodev"},
            "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 1024}],
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}},
            "RestartPolicy": {"Name": "no"},
            "NetworkMode": ns,
        },
    }


def network_body(req: RuntimeRequest, subnet: str) -> dict[str, Any]:
    return {
        "Name": req.namespace,
        "Driver": "bridge",
        "Internal": True,
        "EnableIPv6": False,
        "IPAM": {"Driver": "default", "Config": [{"Subnet": subnet}]},
        "Labels": labels(req, "network"),
    }

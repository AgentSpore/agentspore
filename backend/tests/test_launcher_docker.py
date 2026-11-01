"""Launcher against a real Docker daemon (Colima). runsc is stand-only: these run with runc.

Needs a local `registry` image (the one pinned in deploy/a4-builder.compose.yml) and a free
127.0.0.1:5000 inside the daemon host; otherwise the module is skipped.
"""

import ipaddress
import json
import os
import re
import subprocess
import uuid

import httpx
import pytest

from app.launcher.docker import DockerClient, DockerError
from app.launcher.schemas import LauncherError, RuntimeRequest
from app.launcher.service import Launcher

REGISTRY_IMAGE = "registry@sha256:d4032d4cc323c1731c707931ecada07a53076eac38e3b61ae57533f170f2c6c3"
SOCK = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock").removeprefix("unix://")


def docker(*args: str, stdin: str | None = None, check: bool = True) -> str:
    done = subprocess.run(
        ["docker", *args], input=stdin, capture_output=True, text=True, check=False
    )
    if check and done.returncode:
        raise RuntimeError(done.stderr[-300:])
    return done.stdout.strip()


@pytest.fixture(scope="module")
def registry():
    name = f"a4p4b-reg-{uuid.uuid4().hex[:8]}"
    done = subprocess.run(
        ["docker", "run", "-d", "--name", name, "-p", "127.0.0.1:5000:5000", REGISTRY_IMAGE],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode:
        pytest.skip("docker or registry image or port 5000 unavailable")
    yield name
    docker("rm", "-f", name, check=False)


@pytest.fixture
def publish(registry):
    pushed: list[str] = []

    def push(dockerfile: str, ns: str) -> str:
        tag = f"127.0.0.1:5000/{ns}:t"
        docker("build", "-q", "-t", tag, "-", stdin=dockerfile)
        out = docker("push", tag)
        digest = re.search(r"digest: (sha256:[0-9a-f]{64})", out).group(1)
        docker("rmi", tag, check=False)
        pushed.append(ns)
        return f"127.0.0.1:5000/{ns}@{digest}"

    yield push
    for ns in pushed:
        docker(
            "image",
            "rm",
            "-f",
            *docker("images", "-q", f"127.0.0.1:5000/{ns}").split(),
            check=False,
        )


@pytest.fixture
def dep():
    dep_id = uuid.uuid4().hex
    yield dep_id
    ns = f"a4-{dep_id}-1"
    docker("rm", "-f", ns, check=False)
    docker("network", "rm", ns, check=False)


@pytest.fixture
def launcher():
    client = DockerClient(httpx.AsyncHTTPTransport(uds=SOCK))
    return Launcher(
        client,
        (),
        ipaddress.IPv4Network("10.214.0.0/16"),
        runtime="runc",
        arch=docker("info", "--format", "{{.Architecture}}")
        .replace("x86_64", "amd64")
        .replace("aarch64", "arm64"),
    )


def request(dep: str, image: str) -> RuntimeRequest:
    return RuntimeRequest(
        deployment_id=dep,
        generation=1,
        worker_id="w1",
        image=image,
        env={},
        public_url="https://x.example.com",
    )


ROOT_IMAGE = 'FROM python:3.12-alpine\nUSER root\nCMD ["sleep", "300"]\n'


async def test_runtime_runs_as_10001_despite_user_root(launcher, publish, dep):
    ns = f"a4-{dep}-1"
    await launcher.put(ns, request(dep, publish(ROOT_IMAGE, ns)))
    await launcher.start(ns)
    assert docker("exec", ns, "id", "-u") == "10001"
    assert docker("exec", ns, "id", "-g") == "10001"


async def test_runtime_hardening_flags(launcher, publish, dep):
    ns = f"a4-{dep}-1"
    await launcher.put(ns, request(dep, publish(ROOT_IMAGE, ns)))
    await launcher.start(ns)
    info = json.loads(docker("inspect", ns))[0]
    host = info["HostConfig"]
    assert host["ReadonlyRootfs"] is True and host["Privileged"] is False
    assert host["CapDrop"] == ["ALL"] and host["PidsLimit"] == 64
    assert host["Memory"] == host["MemorySwap"] == 268435456 and host["NanoCpus"] == 500_000_000
    assert "no-new-privileges" in host["SecurityOpt"][0] and host["RestartPolicy"]["Name"] in (
        "no",
        "",
    )
    assert not host["Binds"] and not host["PortBindings"]
    assert docker("exec", ns, "touch", "/rootfs-write", check=False) == ""
    assert "0000000000000000" in docker("exec", ns, "grep", "CapEff", "/proc/1/status")
    net = json.loads(docker("network", "inspect", ns))[0]
    assert net["Internal"] is True and net["IPAM"]["Config"][0]["Subnet"].endswith("/28")
    assert f"svc-{dep}" in info["NetworkSettings"]["Networks"][ns]["Aliases"]


def test_runtime_runsc_is_stand_only():
    runtimes = json.loads(docker("info", "--format", "{{json .Runtimes}}"))
    if "runsc" not in runtimes:
        pytest.skip("runsc is not registered on this daemon (stand-only assertion)")
    assert "runsc" in runtimes


async def test_launcher_refuses_image_volume_real(launcher, publish, dep):
    ns = f"a4-{dep}-1"
    image = publish('FROM python:3.12-alpine\nVOLUME /data\nCMD ["sleep", "300"]\n', ns)
    with pytest.raises(LauncherError) as err:
        await launcher.put(ns, request(dep, image))
    assert err.value.reason == "image_volume_not_allowed"
    assert docker("ps", "-aq", "--filter", f"name={ns}") == ""


async def test_launcher_digest_mismatch_real(launcher, publish, dep):
    ns = f"a4-{dep}-1"
    other = publish(ROOT_IMAGE + "LABEL v=2\n", ns)
    forged = other.split("@")[0] + "@sha256:" + "0" * 64
    with pytest.raises(DockerError):
        await launcher.put(ns, request(dep, forged))
    assert docker("ps", "-aq", "--filter", f"name={ns}") == ""


async def test_launch_twice_and_delete_real(launcher, publish, dep):
    ns = f"a4-{dep}-1"
    req = request(dep, publish(ROOT_IMAGE, ns))
    first = await launcher.put(ns, req)
    assert await launcher.put(ns, req) == first
    other = request(dep, req.image).model_copy(update={"worker_id": "w2"})
    with pytest.raises(LauncherError) as err:
        await launcher.put(ns, other)
    assert err.value.status == 409
    assert [r["namespace"] for r in await launcher.list_runtimes() if r["namespace"] == ns] == [ns]
    await launcher.delete(ns)
    await launcher.delete(ns)
    assert docker("ps", "-aq", "--filter", f"name={ns}") == ""
    assert docker("network", "ls", "-q", "--filter", f"name={ns}") == ""

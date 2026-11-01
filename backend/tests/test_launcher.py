"""Launcher API against an in-memory Docker daemon (httpx.MockTransport)."""

import asyncio
import ipaddress
import json
import subprocess
import sys

import httpx
import pytest

from app.launcher.__main__ import configure
from app.launcher.app import build_app
from app.launcher.docker import DockerClient
from app.launcher.schemas import RuntimeRequest
from app.launcher.service import Launcher
from app.launcher.spec import container_body
from tests.test_launcher_fake import DEP, DIGEST, IMAGE, NS, FakeDaemon

POOL = ipaddress.IPv4Network("10.214.0.0/16")
BODY = {
    "deployment_id": DEP,
    "generation": 1,
    "worker_id": "w1",
    "image": IMAGE,
    "env": {"FOO": "bar"},
    "public_url": "https://svc.example.com",
}


@pytest.fixture
def daemon():
    return FakeDaemon()


@pytest.fixture
async def api(daemon):
    launcher = Launcher(
        DockerClient(daemon.transport()), ("agentspore-backend", "agentspore-a4-worker"), POOL
    )
    transport = httpx.ASGITransport(app=build_app(launcher))
    async with httpx.AsyncClient(transport=transport, base_url="http://launcher") as client:
        yield client


async def put(api, body, ns=NS):
    return await api.put(f"/v1/runtimes/{ns}", content=json.dumps(body))


async def test_launcher_hostconfig_constants(api, daemon):
    resp = await put(api, {**BODY, "env": {"FOO": "bar", "USER": "root"}})
    assert resp.status_code == 200
    (sent,) = daemon.bodies("POST", "/containers/create")
    host = sent["HostConfig"]
    assert (
        host["Runtime"] == "runsc"
        and host["Privileged"] is False
        and host["ReadonlyRootfs"] is True
    )
    assert host["CapDrop"] == ["ALL"] and host["SecurityOpt"] == ["no-new-privileges"]
    assert host["NanoCpus"] == 500_000_000 and host["Memory"] == host["MemorySwap"] == 268435456
    assert host["PidsLimit"] == 64 and host["RestartPolicy"] == {"Name": "no"}
    assert host["Tmpfs"] == {"/tmp": "size=32m,noexec,nosuid,nodev"}
    assert host["Ulimits"] == [{"Name": "nofile", "Soft": 1024, "Hard": 1024}]
    assert host["LogConfig"]["Config"] == {"max-size": "10m", "max-file": "3"}
    assert host["NetworkMode"] == NS
    for absent in (
        "Binds",
        "Mounts",
        "Devices",
        "PortBindings",
        "VolumesFrom",
        "CapAdd",
        "PidMode",
        "IpcMode",
    ):
        assert absent not in host
    assert sent["User"] == "10001:10001" and sent["Image"] == IMAGE
    assert sent["Healthcheck"] == {"Test": ["NONE"]}
    assert sent["Labels"]["com.agentspore.a4.role"] == "runtime"
    env = dict(e.split("=", 1) for e in sent["Env"])
    assert env["PORT"] == "8080" and env["HOSTNAME"] == f"svc-{DEP}" and env["FOO"] == "bar"
    assert sent["NetworkingConfig"]["EndpointsConfig"][NS]["Aliases"] == [f"svc-{DEP}"]


def test_container_body_runtime_is_a_parameter_defaulting_to_runsc():
    req = RuntimeRequest(**BODY)
    assert container_body(req)["HostConfig"]["Runtime"] == "runsc"
    assert container_body(req, "runc")["HostConfig"]["Runtime"] == "runc"


@pytest.mark.parametrize(
    "extra",
    [
        {"privileged": True},
        {"Privileged": True},
        {"cap_add": ["SYS_ADMIN"]},
        {"network_mode": "host"},
        {"binds": ["/:/host"]},
        {"Mounts": [{"Type": "bind"}]},
        {"VolumesFrom": ["x"]},
        {"HostConfig": {}},
        {"user": "0"},
        {"attach": ["evil"]},
    ],
)
async def test_launcher_rejects_docker_fields(api, daemon, extra):
    resp = await put(api, {**BODY, **extra})
    assert resp.status_code == 422 and resp.json()["reason"] == "invalid_request"
    assert daemon.calls == []


async def test_launcher_rejects_case_and_dup_keys(api, daemon):
    assert (await put(api, {**BODY, "Image": IMAGE})).status_code == 422
    dup = '{"deployment_id":"%s","generation":1,"generation":2,"worker_id":"w1","image":"%s","env":{},"public_url":"https://a.io"}'
    assert (await api.put(f"/v1/runtimes/{NS}", content=dup % (DEP, IMAGE))).status_code == 422
    dup_env = json.dumps(BODY).replace('"FOO": "bar"', '"FOO": "a", "FOO": "b"')
    assert (await api.put(f"/v1/runtimes/{NS}", content=dup_env)).status_code == 422
    escaped = json.dumps(BODY).replace('"worker_id"', '"worker_id": "x", "\\u0077orker_id"')
    assert (await api.put(f"/v1/runtimes/{NS}", content=escaped)).status_code == 422
    assert daemon.calls == []


@pytest.mark.parametrize(
    "image",
    [
        f"127.0.0.1:5000/{NS}:latest",
        f"localhost:5000/{NS}@sha256:{DIGEST}",
        f"evil.example/{NS}@sha256:{DIGEST}",
        f"127.0.0.1:5000/a4-{'cd' * 16}-1@sha256:{DIGEST}",
        f"127.0.0.1:5000/{NS}@sha256:{DIGEST[:-1]}",
        f"127.0.0.1:5000/{NS}@sha256:{DIGEST}\n",
        f"127.0.0.1:5000/{NS}-x@sha256:{DIGEST}",
        f"127.0.0.1:5000/{NS}",
    ],
)
async def test_launcher_image_not_allowed(api, daemon, image):
    resp = await put(api, {**BODY, "image": image})
    assert resp.status_code == 422 and resp.json()["reason"] == "image_not_allowed"
    assert daemon.calls == []


@pytest.mark.parametrize(
    "env", [{"PORT": "1"}, {"HTTPS_PROXY": "x"}, {"lower": "x"}, {"A B": "x"}, {"A": "x\x00"}]
)
async def test_launcher_env_rules(api, env):
    assert (await put(api, {**BODY, "env": env})).status_code == 422


async def test_launcher_env_limits(api):
    many = {f"K{i}": "v" for i in range(65)}
    assert (await put(api, {**BODY, "env": many})).status_code == 422
    assert (await put(api, {**BODY, "env": {"K": "v" * 20000}})).status_code == 422


async def test_launcher_body_cap(api):
    resp = await api.put(f"/v1/runtimes/{NS}", content=b" " * 16385)
    assert resp.status_code == 422 and resp.json()["reason"] == "body_too_large"


async def test_launcher_ns_mismatch_403(api, daemon):
    other = f"a4-{DEP}-2"
    resp = await put(api, BODY, ns=other)
    assert resp.status_code == 403 and resp.json()["reason"] == "ns_mismatch"
    assert (await api.post("/v1/runtimes/not-a-ns/start")).status_code == 403
    assert daemon.calls == []


async def test_launcher_attach_allowlist(api, daemon):
    await put(api, BODY)
    connects = daemon.bodies("POST", rf"/networks/{NS}/connect")
    assert [c["Container"] for c in connects] == ["agentspore-backend", "agentspore-a4-worker"]
    assert set(daemon.networks[NS]["Containers"]) == {"agentspore-backend", "agentspore-a4-worker"}
    resp = await put(api, {**BODY, "attach": ["evil"]})
    assert resp.status_code == 422


async def test_network_is_internal_28_from_pool_and_skips_used(api, daemon):
    daemon.networks["other"] = {"sub": "10.214.0.0/28", "Labels": {}, "Containers": {}}
    await put(api, BODY)
    (net,) = daemon.bodies("POST", "/networks/create")
    assert net["Internal"] is True and net["EnableIPv6"] is False
    assert net["IPAM"]["Config"] == [{"Subnet": "10.214.0.16/28"}]


async def test_launcher_digest_mismatch(api, daemon):
    daemon.image_info["RepoDigests"] = [f"127.0.0.1:5000/{NS}@sha256:{'d' * 64}"]
    resp = await put(api, BODY)
    assert resp.status_code == 422 and resp.json()["reason"] == "digest_mismatch"
    assert daemon.containers == {} and daemon.networks == {}


async def test_launcher_refuses_image_volume(api, daemon):
    daemon.image_info["Config"] = {"Volumes": {"/data": {}}}
    resp = await put(api, BODY)
    assert resp.status_code == 422 and resp.json()["reason"] == "image_volume_not_allowed"
    assert daemon.containers == {}


@pytest.mark.parametrize("field,value", [("Os", "windows"), ("Architecture", "arm64")])
async def test_launcher_refuses_other_platform(api, daemon, field, value):
    daemon.image_info[field] = value
    assert (await put(api, BODY)).json()["reason"] == "platform_not_allowed"


async def test_launch_twice_same_lease_idempotent(api, daemon):
    first = await put(api, BODY)
    second = await put(api, BODY)
    assert first.json() == second.json() == {"container_id": f"id-{NS}"}
    assert len(daemon.containers) == 1 and len(daemon.bodies("POST", "/networks/create")) == 1


async def test_launch_other_worker_409(api, daemon):
    await put(api, BODY)
    resp = await put(api, {**BODY, "worker_id": "w2"})
    assert resp.status_code == 409 and resp.json()["reason"] == "namespace_owned"


async def test_launch_same_lease_other_image_409(api, daemon):
    other = f"127.0.0.1:5000/{NS}@sha256:{'d' * 64}"
    daemon.image_info["RepoDigests"] = [IMAGE, other]
    await put(api, BODY)
    resp = await put(api, {**BODY, "image": other})
    assert resp.status_code == 409 and resp.json()["reason"] == "namespace_owned"


async def test_launch_same_lease_other_env_409(api, daemon):
    await put(api, BODY)
    assert (await put(api, {**BODY, "env": {"FOO": "other"}})).status_code == 409


async def test_foreign_container_with_ns_name_is_never_touched(api, daemon):
    daemon.containers[NS] = {"Id": "x", "Config": {"Image": "nginx", "Labels": {}}}
    assert (await api.post(f"/v1/runtimes/{NS}/start")).status_code == 409
    assert (await api.delete(f"/v1/runtimes/{NS}")).status_code == 409
    assert NS in daemon.containers


async def test_start_stop_delete_lifecycle_is_idempotent(api, daemon):
    await put(api, BODY)
    assert (await api.post(f"/v1/runtimes/{NS}/start")).status_code == 200
    assert (await api.post(f"/v1/runtimes/{NS}/stop")).status_code == 200
    for _ in range(2):
        assert (await api.delete(f"/v1/runtimes/{NS}")).status_code == 200
    assert daemon.containers == {} and daemon.networks == {}
    assert (await api.post(f"/v1/runtimes/{NS}/start")).status_code == 404
    assert (await api.post(f"/v1/runtimes/{NS}/stop", content=b"{}")).status_code == 422


async def test_list_runtimes_filters_by_launcher_labels(api, daemon):
    await put(api, BODY)
    daemon.containers["foreign"] = {"Id": "f1", "Config": {"Image": "x", "Labels": {}}}
    listing = (await api.get("/v1/runtimes")).json()["runtimes"]
    assert listing == [
        {
            "namespace": NS,
            "container_id": f"id-{NS}",
            "state": "created",
            "deployment": DEP,
            "worker": "w1",
            "generation": "1",
        }
    ]
    (query,) = [q for m, p, q in daemon.queries if p == "/containers/json"]
    flt = json.loads(query["filters"])
    assert flt == {"label": ["com.agentspore.a4.role=runtime", "com.agentspore.a4.launcher=1"]}
    assert query["all"] == "true"


async def test_image_gc_removes_only_unkept_a4_refs(api, daemon):
    keep, drop = IMAGE, f"127.0.0.1:5000/a4-{'ef' * 16}-1@sha256:{'1' * 64}"
    daemon.image_list = [{"RepoDigests": [keep, drop, "nginx@sha256:" + "2" * 64]}]
    resp = await api.post("/v1/images/gc", content=json.dumps({"keep": [keep]}))
    assert resp.json() == {"removed": [drop]}
    assert (
        await api.post("/v1/images/gc", content=json.dumps({"keep": ["nginx"]}))
    ).status_code == 422


async def test_docker_error_is_redacted_with_reason_code():
    def boom(request):
        return httpx.Response(500, content=b"failed Bearer abcdef.ghi token=hunter2")

    launcher = Launcher(DockerClient(httpx.MockTransport(boom)), (), POOL)
    transport = httpx.ASGITransport(app=build_app(launcher))
    async with httpx.AsyncClient(transport=transport, base_url="http://l") as c:
        resp = await c.put(f"/v1/runtimes/{NS}", content=json.dumps(BODY))
    assert resp.status_code == 502
    assert resp.json() == {"reason": "docker_error", "docker_status": 500}
    assert "hunter2" not in resp.text and "abcdef" not in resp.text


async def test_oversized_docker_reply_is_cut():
    def big(request):
        return httpx.Response(200, content=b"x" * (5 * 1024 * 1024))

    launcher = Launcher(DockerClient(httpx.MockTransport(big)), (), POOL)
    transport = httpx.ASGITransport(app=build_app(launcher))
    async with httpx.AsyncClient(transport=transport, base_url="http://l") as c:
        resp = await c.get("/v1/runtimes")
    assert resp.status_code == 502


def test_launcher_imports_no_app_config():
    code = "import sys, app.launcher.__main__; sys.exit('app.core.config' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def other_ns(n: int) -> tuple[str, dict]:
    dep = f"{n:032x}"
    ns = f"a4-{dep}-1"
    image = f"127.0.0.1:5000/{ns}@sha256:{DIGEST}"
    return ns, {**BODY, "deployment_id": dep, "image": image}


async def test_runtime_cap_429_and_existing_ns_not_blocked(daemon):
    launcher = Launcher(DockerClient(daemon.transport()), (), POOL, max_runtimes=2)
    transport = httpx.ASGITransport(app=build_app(launcher))
    async with httpx.AsyncClient(transport=transport, base_url="http://l") as c:
        for n in (1, 2):
            ns, body = other_ns(n)
            daemon.image_info["RepoDigests"] = [body["image"]]
            assert (await c.put(f"/v1/runtimes/{ns}", content=json.dumps(body))).status_code == 200
        daemon.networks["foreign"] = {"sub": "10.214.9.0/28", "Labels": {}, "Containers": {}}
        ns, body = other_ns(3)
        daemon.image_info["RepoDigests"] = [body["image"]]
        over = await c.put(f"/v1/runtimes/{ns}", content=json.dumps(body))
        assert over.status_code == 429 and over.json()["reason"] == "runtime_cap"
        ns, body = other_ns(1)
        daemon.image_info["RepoDigests"] = [body["image"]]
        again = await c.put(f"/v1/runtimes/{ns}", content=json.dumps(body))
        assert again.status_code == 200
    assert len(daemon.networks) == 3


@pytest.mark.parametrize(
    "value,ok",
    [("1", True), ("256", True), ("0", False), ("257", False), ("x", False), ("²", False)],
)
def test_max_runtimes_env_is_parsed_at_startup(monkeypatch, value, ok):
    monkeypatch.setenv("A4_LAUNCHER_MAX_RUNTIMES", value)
    if ok:
        assert configure() is not None
    else:
        with pytest.raises(SystemExit):
            configure()


async def test_concurrent_puts_do_not_double_connect(api, daemon):
    results = await asyncio.gather(*(put(api, BODY) for _ in range(4)))
    assert [r.status_code for r in results] == [200] * 4
    names = [c["Container"] for c in daemon.bodies("POST", rf"/networks/{NS}/connect")]
    assert sorted(names) == sorted(set(names))


async def test_pull_stream_beyond_reply_cap_succeeds(api, daemon):
    daemon.pull_stream = b'{"status":"Downloading"}\n' * (5 * 1024 * 1024 // 25)
    assert (await put(api, BODY)).status_code == 200


async def test_pull_error_line_fails_even_after_a_long_stream(api, daemon):
    daemon.pull_chunk = 10
    daemon.pull_stream = b'{"status":"Downloading"}\n' * 5_000 + b'{"error":"denied"}\n'
    resp = await put(api, BODY)
    assert resp.status_code == 502 and resp.json()["reason"] == "pull_failed"
    assert daemon.containers == {}


async def test_runtime_cap_counts_only_launcher_owned_networks(daemon):
    launcher = Launcher(DockerClient(daemon.transport()), (), POOL, max_runtimes=2)
    for i in range(3):
        name = f"foreign{i}"
        daemon.networks[name] = {"sub": f"10.214.{i + 1}.0/28", "Labels": {}, "Containers": {}}
    transport = httpx.ASGITransport(app=build_app(launcher))
    async with httpx.AsyncClient(transport=transport, base_url="http://l") as c:
        assert (await c.put(f"/v1/runtimes/{NS}", content=json.dumps(BODY))).status_code == 200


async def test_inventory_includes_owned_network_without_container(api, daemon):
    assert (await put(api, BODY)).status_code == 200
    daemon.containers.clear()
    daemon.networks["foreign"] = {"sub": "10.215.0.0/28", "Labels": {}, "Containers": {}}
    listing = (await api.get("/v1/runtimes")).json()
    assert listing["runtimes"] == []
    assert listing["networks"] == [{"namespace": NS}]


async def test_gc_waits_for_pull_to_container_creation():
    daemon = FakeDaemon()
    creating, release = asyncio.Event(), asyncio.Event()

    async def transport(req):
        if req.url.path.endswith("/containers/create"):
            creating.set()
            await release.wait()
        return daemon._handle(req)

    launcher = Launcher(DockerClient(httpx.MockTransport(transport)), (), POOL)
    launching = asyncio.create_task(launcher.put(NS, RuntimeRequest.model_validate(BODY)))
    collecting = None
    try:
        async with asyncio.timeout(5):
            await creating.wait()
            collecting = asyncio.create_task(launcher.gc_images([]))
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(collecting), timeout=0.03)
            assert not any(path == "/images/json" for _, path, _ in daemon.calls)
            release.set()
            await launching
            await collecting
    finally:
        release.set()
        await launching
        if collecting is not None:
            await collecting


async def test_gc_preserves_docker_in_use_conflict_without_force():
    calls = []

    async def boundary(req):
        calls.append(req)
        if req.method == "GET":
            return httpx.Response(200, json=[{"RepoDigests": [IMAGE]}])
        return httpx.Response(409, json={"message": "image is being used by container"})

    launcher = Launcher(DockerClient(httpx.MockTransport(boundary)), (), POOL)
    assert await launcher.gc_images([]) == []
    assert len(calls) == 2 and calls[1].method == "DELETE"
    assert "force" not in calls[1].url.params

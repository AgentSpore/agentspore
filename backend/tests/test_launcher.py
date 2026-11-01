"""Launcher API against an in-memory Docker daemon (httpx.MockTransport)."""

import ipaddress
import json

import httpx
import pytest

from app.launcher.app import build_app
from app.launcher.docker import DockerClient
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
    from app.launcher.schemas import RuntimeRequest

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
    await api.get("/v1/runtimes")
    (call,) = [c for c in daemon.calls if c[1] == "/containers/json"]
    assert call[0] == "GET"
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
    assert resp.status_code == 502 and resp.json()["reason"] == "docker_error"
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
    import subprocess
    import sys

    code = "import sys, app.launcher.__main__; sys.exit('app.core.config' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0

"""Static gate: the `a4-worker` service in the main compose file."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def worker():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["a4-worker"]


def test_builder_defaults_to_fake(worker):
    assert worker["environment"]["A4_BUILDER"] == "${A4_BUILDER:-fake}"


def test_tls_dir_is_a_read_only_bind_that_must_exist(worker):
    (mount,) = [
        v for v in worker["volumes"] if isinstance(v, dict) and v["target"] == "/run/a4-builder-tls"
    ]
    assert mount["type"] == "bind"
    assert mount["read_only"] is True
    assert mount["bind"]["create_host_path"] is False


def test_memory_limit_covers_the_tmpfs(worker):
    assert worker["deploy"]["resources"]["limits"]["memory"] == "6g"


def test_address_and_mirror_host_are_passed_through(worker):
    env = worker["environment"]
    assert env["A4_BUILDKIT_ADDR"].startswith("${A4_BUILDKIT_ADDR:-")
    assert env["A4_PKG_MIRROR_HOST"] == "${A4_PKG_MIRROR_HOST:-10.213.77.12}"
    assert "A4_BUILDER_PROXY_URL" not in env


def test_out_dir_is_a_4g_tmpfs(worker):
    assert worker["environment"]["A4_BUILDER_OUT_DIR"] == "/var/lib/a4-out"
    (entry,) = [t for t in worker["tmpfs"] if t.startswith("/var/lib/a4-out")]
    assert "size=4g" in entry.split(":", 1)[1].split(",")


def test_worker_has_no_docker_socket(worker):
    assert "docker.sock" not in str(worker)


def test_default_cert_source_exists_so_fake_builder_hosts_still_start(worker):
    (mount,) = [
        v for v in worker["volumes"] if isinstance(v, dict) and v["target"] == "/run/a4-builder-tls"
    ]
    default = mount["source"].split(":-", 1)[1].removesuffix("}")
    assert (ROOT / default).is_dir()


def test_worker_shares_the_control_network_with_the_gate_only():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    nets = compose["services"]["a4-worker"]["networks"]
    assert "a4-control" in nets and "a4-builder" not in nets
    control = compose["networks"]["a4-control"]
    assert control["internal"] is True and control["name"] == "a4-control"
    others = [
        n for n, s in compose["services"].items() if "a4-control" in (s.get("networks") or [])
    ]
    assert others == ["a4-worker"]


def test_recycle_gate_defaults_target_the_gate_service(worker):
    env = worker["environment"]
    assert env["A4_RECYCLE_GATE_URL"] == "${A4_RECYCLE_GATE_URL:-http://a4-restart-gate:2375}"


def test_backend_image_carries_buildctl_pinned_by_digest():
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text()
    assert re.search(
        r"^COPY --from=moby/buildkit@sha256:[0-9a-f]{64} "
        r"/usr/bin/buildctl /usr/local/bin/buildctl$",
        dockerfile,
        re.M,
    )


@pytest.fixture(scope="module")
def production_worker():
    return yaml.safe_load((ROOT / "deploy/docker-compose.prod.yml").read_text())["services"][
        "a4-worker"
    ]


def test_production_worker_requires_opt_in_and_immutable_image(production_worker):
    assert production_worker["profiles"] == ["a4"]
    assert production_worker["image"] == (
        "${A4_WORKER_REPOSITORY:-invalid.invalid/a4-worker}@sha256:"
        "${A4_WORKER_DIGEST:-" + "0" * 64 + "}"
    )
    assert production_worker["environment"]["A4_DEPLOY_ENABLED"] == "${A4_DEPLOY_ENABLED:-false}"
    assert production_worker["environment"]["A4_RUNTIME"] == "${A4_RUNTIME:-off}"
    assert production_worker["environment"]["A4_BUILDER"] == "buildkit"


def test_production_worker_declares_no_forwarding_or_published_ports(production_worker):
    assert production_worker["user"] == "10001:10001"
    assert production_worker["read_only"] is True
    assert production_worker["cap_drop"] == ["ALL"]
    assert production_worker["security_opt"] == ["no-new-privileges:true"]
    assert production_worker["sysctls"] == {
        "net.ipv4.ip_forward": "0",
        "net.ipv6.conf.all.forwarding": "0",
    }
    assert not set(production_worker) & {
        "build",
        "ports",
        "expose",
        "privileged",
        "cap_add",
        "network_mode",
        "pid",
    }
    assert "docker.sock" not in str(production_worker)
    assert production_worker["healthcheck"] == {"disable": True}
    assert set(production_worker["networks"]) == {
        "sporeai-network",
        "a4-builder",
        "a4-control",
        "a4-registry-net",
    }


def test_production_worker_writable_paths_and_tls_are_bounded(production_worker):
    mounts = production_worker["volumes"]
    assert len(mounts) == 1
    assert mounts[0]["target"] == "/run/a4-builder-tls"
    assert mounts[0]["read_only"] is True
    assert mounts[0]["bind"]["create_host_path"] is False
    paths = dict(t.split(":", 1) for t in production_worker["tmpfs"])
    assert set(paths) == {"/tmp", "/app/logs", "/var/lib/a4-out"}
    for options in paths.values():
        assert {"uid=10001", "gid=10001", "mode=0700"} <= set(options.split(","))
    assert "size=4g" in paths["/var/lib/a4-out"]
    assert production_worker["deploy"]["resources"]["limits"]["memory"] == "6g"
    env = production_worker["environment"]
    assert env["DEBUG"] == "false"
    assert env["HOME"] == "/tmp"
    assert env["SECRET_KEY"] == "${SECRET_KEY}"
    assert "POSTGRES_PASSWORD" in env["DATABASE_URL"]
    assert "A4_BASE_IMAGE_DIGESTS" in env
    assert not set(env) & {"GITHUB_PAT", "GITHUB_APP_PRIVATE_KEY", "OPENROUTER_API_KEY"}


@pytest.mark.parametrize("enabled", [False, True])
def test_production_compose_renders_without_host_configuration(tmp_path, enabled):
    docker = shutil.which("docker")
    assert docker is not None, "Docker Compose CLI is required for this gate"
    env = {
        "PATH": os.defpath,
        "HOME": str(tmp_path),
        "DOCKER_CONFIG": str(tmp_path),
        "SECRET_KEY": "synthetic-compose-only",
        "POSTGRES_PASSWORD": "synthetic-only",
        "CLOUDFLARE_API_TOKEN": "synthetic-compose-only",
    }
    if enabled:
        env.update(A4_WORKER_REPOSITORY="registry.example/worker", A4_WORKER_DIGEST="a" * 64)
    command = [
        docker,
        "compose",
        "--env-file",
        "/dev/null",
        "-f",
        str(ROOT / "deploy/docker-compose.prod.yml"),
    ]
    if enabled:
        command += ["--profile", "a4"]
    result = subprocess.run(
        command + ["config", "--format", "json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert ("a4-worker" in services) is enabled
    if enabled:
        assert services["a4-worker"]["image"] == "registry.example/worker@sha256:" + "a" * 64
    assert "backend" in services

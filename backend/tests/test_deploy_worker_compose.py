"""Static gate: the `a4-worker` service in the main compose file."""

import re
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


def test_address_and_proxy_are_passed_through(worker):
    env = worker["environment"]
    assert env["A4_BUILDKIT_ADDR"].startswith("${A4_BUILDKIT_ADDR:-")
    assert env["A4_BUILDER_PROXY_URL"].startswith("${A4_BUILDER_PROXY_URL:-")


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

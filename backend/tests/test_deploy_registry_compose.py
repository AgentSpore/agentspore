"""Hardening gate for the A4 registry (`deploy/a4-registry.compose.yml`)."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "deploy" / "a4-builder.compose.yml"
REGISTRY = ROOT / "deploy" / "a4-registry.compose.yml"


@pytest.fixture(scope="module")
def stack() -> dict:
    return yaml.safe_load(REGISTRY.read_text())


@pytest.fixture(scope="module")
def svc(stack) -> dict:
    assert list(stack["services"]) == ["a4-registry-init", "a4-registry"]
    return stack["services"]["a4-registry"]


def test_image_is_registry_3_pinned_by_digest(svc):
    assert re.fullmatch(r"registry@sha256:[0-9a-f]{64}", svc["image"])
    assert "v3.1.2" in REGISTRY.read_text()  # the comment records which release


def test_container_is_locked_down(svc):
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "cap_add" not in svc
    assert "no-new-privileges:true" in svc["security_opt"]
    assert not svc.get("privileged", False)


def test_registry_runs_as_non_root_and_the_volume_is_chowned_first(stack, svc):
    assert svc["user"] == "1000:1000"
    init = stack["services"]["a4-registry-init"]
    assert init["image"] == svc["image"]
    assert init["entrypoint"] == ["chown", "-R", "1000:1000", "/var/lib/registry"]
    assert init["network_mode"] == "none"
    assert init["cap_drop"] == ["ALL"] and init["cap_add"] == ["CHOWN"]
    assert svc["depends_on"]["a4-registry-init"]["condition"] == "service_completed_successfully"


def test_memory_and_pids_are_limited(svc):
    limits = svc["deploy"]["resources"]["limits"]
    assert limits["memory"] and limits["pids"] <= 256
    assert "pids_limit" not in svc


def test_deletes_are_off_and_no_proxy_is_set(svc):
    env = svc["environment"]
    assert env["REGISTRY_STORAGE_DELETE_ENABLED"] == "false"
    assert not [k for k in env if "PROXY" in k.upper()]


def test_writable_paths_are_a_volume_and_a_tmpfs_only(svc):
    assert svc["volumes"] == ["a4-registry-data:/var/lib/registry"]
    assert [t.split(":")[0] for t in svc["tmpfs"]] == ["/tmp"]


def test_port_is_published_on_loopback_only(svc):
    assert svc["ports"] == ["127.0.0.1:5000:5000"]


def test_registry_is_on_neither_control_nor_builder_nor_any_tenant_network(stack, svc):
    assert set(svc["networks"]) == {"a4-registry-net", "a4-registry-host"}
    builder_nets = set(yaml.safe_load(BUILDER.read_text())["networks"])
    assert not (set(svc["networks"]) & builder_nets)  # a4-control and a4-builder both live there
    assert stack["networks"]["a4-registry-net"]["external"] is True
    assert not stack["networks"]["a4-registry-host"].get("internal", False)
    assert list(stack["networks"]) == ["a4-registry-net", "a4-registry-host"]


def test_only_a4_worker_joins_the_registry_network():
    main = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    on_net = [n for n, s in main["services"].items() if "a4-registry-net" in s.get("networks", [])]
    assert on_net == ["a4-worker"]
    assert main["networks"]["a4-registry-net"] == {"name": "a4-registry-net", "internal": True}
    env = main["services"]["a4-worker"]["environment"]
    assert env["A4_REGISTRY_URL"] == "${A4_REGISTRY_URL:-http://a4-registry:5000}"

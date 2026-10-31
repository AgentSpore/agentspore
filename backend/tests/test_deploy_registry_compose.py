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
    assert list(stack["services"]) == ["a4-registry"]
    return stack["services"]["a4-registry"]


def test_image_is_registry_2_pinned_by_digest(svc):
    assert re.fullmatch(r"registry@sha256:[0-9a-f]{64}", svc["image"])
    assert "registry:2.8.3" in REGISTRY.read_text()  # the comment records which release


def test_container_is_locked_down(svc):
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "cap_add" not in svc
    assert "no-new-privileges:true" in svc["security_opt"]
    assert not svc.get("privileged", False)


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


def test_registry_is_on_the_worker_network_and_nothing_tenant_reachable(stack, svc):
    assert set(svc["networks"]) == {"a4-control", "a4-registry-host"}
    builder_nets = set(yaml.safe_load(BUILDER.read_text())["networks"])
    assert not (set(svc["networks"]) & (builder_nets - {"a4-control"}))
    assert stack["networks"]["a4-control"]["external"] is True
    assert not stack["networks"]["a4-registry-host"].get("internal", False)


def test_worker_network_is_the_one_the_main_compose_gives_a4_worker():
    main = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert "a4-control" in main["services"]["a4-worker"]["networks"]
    assert "a4-registry-host" not in str(main["services"]["a4-worker"])

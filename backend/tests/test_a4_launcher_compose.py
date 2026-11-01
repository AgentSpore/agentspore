"""Hardening gate for `deploy/a4-launcher.compose.yml`."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "deploy" / "a4-launcher.compose.yml"


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(FILE.read_text())


@pytest.fixture(scope="module")
def svc(compose):
    return compose["services"]["a4-launcher"]


def test_only_the_launcher_service_exists(compose):
    assert list(compose["services"]) == ["a4-launcher"]


def test_profile_command_and_image(svc):
    assert svc["profiles"] == ["a4"]
    assert svc["command"] == ["/app/.venv/bin/python", "-m", "app.launcher"]
    assert svc["image"].startswith("${A4_LAUNCHER_IMAGE:?")


def test_unprivileged_user_with_docker_group(svc):
    assert svc["user"] == "10001:10001"
    assert svc["group_add"] == [
        "${A4_DOCKER_GID:?set A4_DOCKER_GID to the host docker group id (getent group docker)}"
    ]


def test_hardening_flags(svc):
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "cap_add" not in svc and "privileged" not in svc
    assert svc["security_opt"] == ["no-new-privileges:true"]


def test_resource_limits(svc):
    assert svc["deploy"]["resources"]["limits"] == {"cpus": "0.25", "memory": "128M", "pids": 64}


def test_docker_socket_is_the_only_mount(svc):
    (mount,) = svc["volumes"]
    assert mount["source"] == mount["target"] == "/var/run/docker.sock"
    assert mount["bind"]["create_host_path"] is False


def test_only_a4_control_and_no_host_ports(compose, svc):
    assert svc["networks"] == ["a4-control"]
    assert "ports" not in svc and "network_mode" not in svc
    assert compose["networks"] == {"a4-control": {"external": True, "name": "a4-control"}}


def test_env_has_only_launcher_settings(svc):
    assert set(svc["environment"]) == {"PYTHONDONTWRITEBYTECODE", "A4_ATTACH", "A4_TENANT_POOL"}
    assert svc["environment"]["A4_TENANT_POOL"] == "${A4_TENANT_POOL:-10.214.0.0/16}"
    assert "env_file" not in svc and "secrets" not in svc


def test_healthcheck_probes_healthz(svc):
    assert "/healthz" in " ".join(svc["healthcheck"]["test"])

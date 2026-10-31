"""Static gate: the `a4-worker` service in the main compose file."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def worker():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["a4-worker"]


def test_builder_defaults_to_fake(worker):
    assert worker["environment"]["A4_BUILDER"] == "${A4_BUILDER:-fake}"


def test_tls_dir_is_mounted_read_only(worker):
    mounts = [v for v in worker["volumes"] if isinstance(v, str) and "/run/a4-builder-tls" in v]
    assert len(mounts) == 1
    assert mounts[0].endswith(":/run/a4-builder-tls:ro")


def test_out_dir_is_a_4g_tmpfs(worker):
    assert worker["environment"]["A4_BUILDER_OUT_DIR"] == "/var/lib/a4-out"
    (entry,) = [t for t in worker["tmpfs"] if t.startswith("/var/lib/a4-out")]
    assert "size=4g" in entry.split(":", 1)[1].split(",")


def test_worker_has_no_docker_socket(worker):
    assert "docker.sock" not in str(worker)

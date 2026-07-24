"""Regression tests for the runner identity used by the sandbox reaper.

The identity must survive `docker compose up --force-recreate`, which replaces
the container and therefore its hostname (a container's default hostname is its
own short id). An identity derived from the hostname made the reaper skip every
container created by the previous deployment — i.e. exactly the orphans.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("RUNNER_KEY", "test-runner-key-for-tests")

from config import RunnerSettings, resolve_runner_instance_id  # noqa: E402

OLD_CONTAINER_HOSTNAME = "60b85b7dc997"
NEW_CONTAINER_HOSTNAME = "a91c33f0b112"


def resolve_as(hostname: str, workspace_root: Path) -> str:
    with patch("config.socket.gethostname", return_value=hostname):
        return resolve_runner_instance_id(workspace_root)


def test_identity_survives_container_recreation(tmp_path):
    before = resolve_as(OLD_CONTAINER_HOSTNAME, tmp_path)
    after = resolve_as(NEW_CONTAINER_HOSTNAME, tmp_path)

    assert before == after
    assert before not in (OLD_CONTAINER_HOSTNAME, NEW_CONTAINER_HOSTNAME)


def test_separate_deployments_get_separate_identities(tmp_path):
    first = resolve_as(OLD_CONTAINER_HOSTNAME, tmp_path / "runner-a")
    second = resolve_as(OLD_CONTAINER_HOSTNAME, tmp_path / "runner-b")

    assert first != second


def test_unwritable_workspace_falls_back_to_hostname(tmp_path):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("")

    assert resolve_as(OLD_CONTAINER_HOSTNAME, blocked / "agents") == OLD_CONTAINER_HOSTNAME


def test_explicit_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_INSTANCE_ID", "fms-runner")

    settings = RunnerSettings(runner_key="x", workspace_root=tmp_path)

    assert settings.runner_instance_id == "fms-runner"


def test_settings_resolve_identity_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNNER_INSTANCE_ID", raising=False)

    with patch("config.socket.gethostname", return_value=OLD_CONTAINER_HOSTNAME):
        settings = RunnerSettings(runner_key="x", workspace_root=tmp_path)

    assert settings.runner_instance_id == resolve_as(NEW_CONTAINER_HOSTNAME, tmp_path)

"""Unit tests for sandbox container labelling and orphan reaping.

Pure-Python — the docker SDK client is fully mocked, no daemon required.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import docker
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("RUNNER_KEY", "test-runner-key-for-tests")

from sandbox import (  # noqa: E402
    LABEL_HOSTED_ID,
    LABEL_MARKER,
    LABEL_RUNNER_ID,
    SecureDockerSandbox,
    reap_orphan_sandboxes,
)

RUNNER_ID = "runner-under-test"


@pytest.fixture
def fake_settings():
    settings = MagicMock()
    settings.container_mem_limit = "512m"
    settings.container_cpu_quota = 50000
    settings.container_cpu_period = 100000
    settings.container_pids_limit = 200
    settings.container_user = "sandbox"
    settings.sandbox_network_name = "sandbox_net"
    settings.runner_instance_id = RUNNER_ID
    settings.sandbox_reap_grace_seconds = 300
    return settings


@pytest.fixture
def docker_client():
    client = MagicMock()
    client.images.get.return_value = MagicMock()
    client.containers.run.return_value = MagicMock(id="abc123")
    client.containers.list.return_value = []
    return client


def make_container(labels: dict, age_seconds: float) -> MagicMock:
    created = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    container = MagicMock()
    container.labels = labels
    # Docker reports nanosecond precision, which datetime cannot parse directly.
    container.attrs = {"Created": created.strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z"}
    return container


def run_kwargs(client) -> dict:
    assert client.containers.run.called
    return client.containers.run.call_args[1]


class TestSandboxLabels:
    def test_container_is_labelled_for_reaping(self, fake_settings, docker_client):
        with patch("main.settings", fake_settings), \
             patch("main.docker.from_env", return_value=docker_client):
            sandbox = SecureDockerSandbox(
                image="agentspore-sandbox:latest",
                work_dir="/workspace",
                volumes={"/data/agents/hosted-42": "/workspace"},
                auto_remove=True,
            )
            sandbox._ensure_runtime_image = MagicMock(return_value="agentspore-sandbox:latest")
            sandbox._ensure_container()

        labels = run_kwargs(docker_client)["labels"]
        assert labels[LABEL_MARKER] == "true"
        assert labels[LABEL_HOSTED_ID] == "hosted-42"
        assert labels[LABEL_RUNNER_ID] == RUNNER_ID


class TestReaper:
    def _reap(self, fake_settings, docker_client, live_hosted_ids=()):
        with patch("main.settings", fake_settings), \
             patch("main.docker.from_env", return_value=docker_client):
            return reap_orphan_sandboxes(live_hosted_ids)

    def test_removes_labelled_orphan_older_than_grace(self, fake_settings, docker_client):
        orphan = make_container(
            {LABEL_MARKER: "true", LABEL_HOSTED_ID: "gone", LABEL_RUNNER_ID: RUNNER_ID},
            age_seconds=86400,
        )
        docker_client.containers.list.return_value = [orphan]

        assert self._reap(fake_settings, docker_client) == 1
        orphan.remove.assert_called_once_with(force=True)

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                ({LABEL_MARKER: "true", LABEL_HOSTED_ID: "alive", LABEL_RUNNER_ID: RUNNER_ID},
                 86400, {"alive"}), id="restored-session",
            ),
            pytest.param(
                ({LABEL_MARKER: "true", LABEL_HOSTED_ID: "fresh", LABEL_RUNNER_ID: RUNNER_ID},
                 10, ()), id="inside-grace-period",
            ),
            pytest.param(
                ({LABEL_MARKER: "true", LABEL_HOSTED_ID: "x", LABEL_RUNNER_ID: "other-runner"},
                 86400, ()), id="another-runner-instance",
            ),
            pytest.param(({}, 86400, ()), id="unlabelled"),
        ],
    )
    def test_keeps_container(self, fake_settings, docker_client, case):
        labels, age_seconds, live_hosted_ids = case
        kept = make_container(labels, age_seconds)
        docker_client.containers.list.return_value = [kept]

        assert self._reap(fake_settings, docker_client, live_hosted_ids) == 0
        kept.remove.assert_not_called()

    def test_listing_is_restricted_to_our_marker_label(self, fake_settings, docker_client):
        self._reap(fake_settings, docker_client)

        kwargs = docker_client.containers.list.call_args[1]
        assert kwargs["filters"] == {"label": f"{LABEL_MARKER}=true"}

    def test_list_failure_does_not_break_startup(self, fake_settings, docker_client):
        docker_client.containers.list.side_effect = docker.errors.DockerException("no daemon")

        assert self._reap(fake_settings, docker_client) == 0

    def test_remove_failure_does_not_break_startup(self, fake_settings, docker_client):
        vanished = make_container(
            {LABEL_MARKER: "true", LABEL_HOSTED_ID: "gone", LABEL_RUNNER_ID: RUNNER_ID},
            age_seconds=86400,
        )
        vanished.remove.side_effect = docker.errors.NotFound("already gone")
        docker_client.containers.list.return_value = [vanished]

        assert self._reap(fake_settings, docker_client) == 0

"""Unit tests for sandbox container security configuration.

Tests are pure-Python — no real Docker daemon required. The docker SDK client
is fully mocked so these pass in CI without Docker.

Coverage:
  - SecureDockerSandbox spawns container with correct security params
  - read_only=True enforced
  - network=sandbox_net enforced
  - user=sandbox enforced
  - cap_drop=["ALL"], cap_add absent/empty
  - no-new-privileges:true in security_opt
  - tmpfs /tmp present
  - resource limits: mem_limit, cpu_quota, cpu_period, pids_limit
  - NET_RAW not in cap_add (removed from earlier version)
  - is_command_safe: UX-hint check (not a security boundary)
  - BLOCKED_COMMANDS: substring detection works
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# main.py calls get_settings() at module level. The linter-modified config.py
# now requires RUNNER_KEY (no default). Set a dummy value so the import
# succeeds in the test environment without a real .env file.
os.environ.setdefault("RUNNER_KEY", "test-runner-key-for-tests")


def _make_fake_settings(**overrides) -> MagicMock:
    """Build a MagicMock that mimics RunnerSettings without loading .env."""
    defaults = dict(
        workspace_root=Path("/tmp/test-agents"),
        docker_image="agentspore-sandbox:latest",
        container_mem_limit="512m",
        container_cpu_quota=50000,
        container_cpu_period=100000,
        container_pids_limit=200,
        container_user="sandbox",
        sandbox_network_name="sandbox_net",
        runner_key="test-runner-key",
        agentspore_url="https://agentspore.com",
        openai_api_key="",
        openai_base_url="",
        docker_host="",
        agent_disk_soft_mb=150,
        agent_disk_hard_mb=200,
        agent_disk_quota_enabled=False,
        max_agents=40,
        chat_timeout=120,
        idle_timeout_seconds=1800,
        default_model="mistralai/mistral-nemo",
        default_budget_usd=1.0,
        default_heartbeat_seconds=3600,
    )
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _make_mock_docker_client():
    """Build a minimal docker SDK mock that records containers.run() kwargs."""
    client = MagicMock()
    client.images.get.return_value = MagicMock()
    mock_container = MagicMock()
    mock_container.id = "abc123"
    client.containers.run.return_value = mock_container
    return client


def _captured_run_kwargs(client_mock) -> dict:
    assert client_mock.containers.run.called, "containers.run was not called"
    _, kwargs = client_mock.containers.run.call_args
    return kwargs


# ── SecureDockerSandbox spawn tests ───────────────────────────────────────


class TestSecureDockerSandboxSpawn:
    """Assert that _ensure_container() passes the expected security kwargs."""

    def _invoke(self, **settings_overrides):
        """
        Patch main.settings + docker.from_env, then call _ensure_container().
        Returns run_kwargs dict.
        """
        fake_settings = _make_fake_settings(**settings_overrides)
        mock_client = _make_mock_docker_client()

        with patch("main.settings", fake_settings), \
             patch("main.docker.from_env", return_value=mock_client):
            # Import here so module-level `settings` is already patched
            from main import SecureDockerSandbox
            sandbox = SecureDockerSandbox(
                image="agentspore-sandbox:latest",
                work_dir="/workspace",
                volumes={"/tmp/test-agents/agent1": "/workspace"},
                auto_remove=True,
            )
            sandbox._ensure_runtime_image = MagicMock(return_value="agentspore-sandbox:latest")
            sandbox._ensure_container()

        return _captured_run_kwargs(mock_client)

    def test_read_only_root_fs(self):
        kwargs = self._invoke()
        assert kwargs.get("read_only") is True

    def test_tmpfs_tmp_present(self):
        kwargs = self._invoke()
        tmpfs = kwargs.get("tmpfs", {})
        assert "/tmp" in tmpfs
        assert "size=100m" in tmpfs["/tmp"]

    def test_network_sandbox_net(self):
        kwargs = self._invoke()
        assert kwargs.get("network") == "sandbox_net"

    def test_user_sandbox(self):
        kwargs = self._invoke()
        assert kwargs.get("user") == "sandbox"

    def test_cap_drop_all(self):
        kwargs = self._invoke()
        assert "ALL" in kwargs.get("cap_drop", [])

    def test_no_cap_add_net_raw(self):
        """NET_RAW was removed — must not appear in cap_add."""
        kwargs = self._invoke()
        cap_add = kwargs.get("cap_add", []) or []
        assert "NET_RAW" not in cap_add

    def test_no_new_privileges_security_opt(self):
        kwargs = self._invoke()
        assert "no-new-privileges:true" in kwargs.get("security_opt", [])

    def test_mem_limit(self):
        kwargs = self._invoke()
        assert kwargs.get("mem_limit") == "512m"

    def test_cpu_quota_and_period(self):
        kwargs = self._invoke()
        assert kwargs.get("cpu_quota") == 50000
        assert kwargs.get("cpu_period") == 100000

    def test_pids_limit(self):
        kwargs = self._invoke()
        assert kwargs.get("pids_limit") == 200

    def test_custom_network_name(self):
        """SANDBOX_NETWORK_NAME flows through to network= param."""
        kwargs = self._invoke(sandbox_network_name="custom_net")
        assert kwargs.get("network") == "custom_net"


# ── is_command_safe (C4 UX-hint) tests ───────────────────────────────────


class TestIsCommandSafe:
    """Verify the substring UX-hint check. NOT a security boundary."""

    @pytest.fixture(autouse=True)
    def _patch_settings(self):
        fake = _make_fake_settings()
        with patch("main.settings", fake):
            yield

    def test_safe_command_passes(self):
        from main import is_command_safe
        ok, reason = is_command_safe("ls -la /workspace")
        assert ok is True
        assert reason == ""

    def test_blocked_rm_rf_root(self):
        from main import is_command_safe
        ok, reason = is_command_safe("rm -rf /")
        assert ok is False
        assert "rm -rf /" in reason

    def test_blocked_mkfs(self):
        from main import is_command_safe
        ok, _ = is_command_safe("mkfs.ext4 /dev/sda1")
        assert ok is False

    def test_blocked_etc_shadow(self):
        from main import is_command_safe
        ok, _ = is_command_safe("cat /etc/shadow")
        assert ok is False

    def test_blocked_shutdown(self):
        from main import is_command_safe
        ok, _ = is_command_safe("shutdown now")
        assert ok is False

    def test_case_insensitive(self):
        from main import is_command_safe
        ok, _ = is_command_safe("SHUTDOWN -h now")
        assert ok is False

    def test_bypass_via_tab_passes_hint_check(self):
        """
        Demonstrates the documented bypass: tab between rm and -rf is NOT caught.
        This is expected — the comment in main.py acknowledges it.
        The container isolation is the actual defence.
        """
        from main import is_command_safe
        ok, _ = is_command_safe("rm\t-rf /")
        # UX hint does NOT catch this — documented limitation.
        assert ok is True

    def test_empty_command_safe(self):
        from main import is_command_safe
        ok, _ = is_command_safe("")
        assert ok is True

    def test_python_script_safe(self):
        from main import is_command_safe
        ok, _ = is_command_safe("python3 main.py --port 8080")
        assert ok is True

"""Unit tests for DiskQuotaManager.

Tests are pure-Python — no Docker, no testcontainers, no real filesystem du.
du output is mocked via unittest.mock.patch so the suite runs without root
or a /data/agents directory.

Coverage:
  - measure_usage_mb: parses du -sb bytes output correctly
  - check_quota: under soft / between soft and hard / at hard / way over
  - cache: stale entries trigger fresh du; fresh entries are returned
  - is_checkpoint_path: bypass detection
  - watcher_loop: soft and hard breach callbacks fire (async smoke test)
  - Feature flag disabled: all checks pass through
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quota import DiskQuotaManager, CHECKPOINT_BYPASS_PREFIXES  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────────────────

def make_manager(
    soft_mb: int = 150,
    hard_mb: int = 200,
    enabled: bool = True,
    agentspore_url: str = "",
    runner_key: str = "",
) -> DiskQuotaManager:
    return DiskQuotaManager(
        workspace_root=Path("/data/agents"),
        soft_mb=soft_mb,
        hard_mb=hard_mb,
        enabled=enabled,
        agentspore_url=agentspore_url,
        runner_key=runner_key,
    )


def _du_result(bytes_used: int) -> MagicMock:
    """Build a fake subprocess.CompletedProcess for du -sb."""
    result = MagicMock()
    result.returncode = 0
    result.stdout = f"{bytes_used}\t/data/agents/abc\n"
    return result


HOSTED_ID = "abc-123"


# ── measure_usage_mb ──────────────────────────────────────────────────────

class TestMeasureUsageMb:
    def test_parses_bytes_and_converts_to_mb(self, tmp_path):
        mgr = make_manager()
        mgr._workspace_root = tmp_path
        (tmp_path / HOSTED_ID).mkdir()

        with patch("subprocess.run", return_value=_du_result(104_857_600)):  # 100 MB
            usage = mgr.measure_usage_mb(HOSTED_ID)

        assert abs(usage - 100.0) < 0.01

    def test_missing_workspace_returns_zero(self, tmp_path):
        mgr = make_manager()
        mgr._workspace_root = tmp_path
        # directory does not exist
        usage = mgr.measure_usage_mb("nonexistent-id")
        assert usage == 0.0

    def test_du_failure_returns_zero(self, tmp_path):
        mgr = make_manager()
        mgr._workspace_root = tmp_path
        (tmp_path / HOSTED_ID).mkdir()

        bad = MagicMock()
        bad.returncode = 1
        bad.stdout = ""
        with patch("subprocess.run", return_value=bad):
            usage = mgr.measure_usage_mb(HOSTED_ID)

        assert usage == 0.0

    def test_updates_cache(self, tmp_path):
        mgr = make_manager()
        mgr._workspace_root = tmp_path
        (tmp_path / HOSTED_ID).mkdir()

        with patch("subprocess.run", return_value=_du_result(52_428_800)):  # 50 MB
            mgr.measure_usage_mb(HOSTED_ID)

        cached = mgr.get_cached_usage_mb(HOSTED_ID)
        assert cached is not None
        assert abs(cached - 50.0) < 0.01


# ── check_quota ───────────────────────────────────────────────────────────

class TestCheckQuota:
    """Parametrised: four regions relative to soft=150/hard=200 MB."""

    def _check_with_usage(self, usage_bytes: int) -> tuple[float, bool]:
        mgr = make_manager(soft_mb=150, hard_mb=200)
        mgr._workspace_root = Path("/data/agents")
        with patch.object(mgr, "measure_usage_mb", return_value=usage_bytes / (1024 * 1024)):
            # Force cache miss
            usage_mb, allowed = mgr.check_quota(HOSTED_ID)
        return usage_mb, allowed

    def test_under_soft_limit(self):
        """50 MB — well below soft. Write allowed."""
        usage_mb, allowed = self._check_with_usage(50 * 1024 * 1024)
        assert allowed is True
        assert usage_mb < 150

    def test_between_soft_and_hard(self):
        """175 MB — past soft, below hard. Write still allowed."""
        usage_mb, allowed = self._check_with_usage(175 * 1024 * 1024)
        assert allowed is True
        assert 150 <= usage_mb < 200

    def test_at_hard_limit(self):
        """Exactly 200 MB — at hard. Write blocked (usage >= hard_mb)."""
        usage_mb, allowed = self._check_with_usage(200 * 1024 * 1024)
        assert allowed is False

    def test_way_over_hard_limit(self):
        """500 MB — massively over. Blocked."""
        usage_mb, allowed = self._check_with_usage(500 * 1024 * 1024)
        assert allowed is False

    def test_uses_cache_when_fresh(self):
        """Fresh cache entry should be returned without calling du."""
        mgr = make_manager(soft_mb=150, hard_mb=200)
        # Inject a fresh 100 MB cache entry
        mgr._cache[HOSTED_ID] = (100 * 1024 * 1024, time.monotonic())

        called = []
        original = mgr.measure_usage_mb

        def spy(hid):
            called.append(hid)
            return original(hid)

        mgr.measure_usage_mb = spy  # type: ignore[assignment]
        usage_mb, allowed = mgr.check_quota(HOSTED_ID)

        assert not called, "du should not be called when cache is fresh"
        assert allowed is True
        assert abs(usage_mb - 100.0) < 0.01

    def test_stale_cache_triggers_fresh_du(self, tmp_path):
        """Expired cache entry should re-run du."""
        mgr = make_manager(soft_mb=150, hard_mb=200)
        mgr._workspace_root = tmp_path
        (tmp_path / HOSTED_ID).mkdir()

        # Inject a stale entry (timestamp far in the past)
        mgr._cache[HOSTED_ID] = (50 * 1024 * 1024, time.monotonic() - 9999)

        with patch("subprocess.run", return_value=_du_result(80 * 1024 * 1024)) as mock_du:
            usage_mb, _ = mgr.check_quota(HOSTED_ID)

        mock_du.assert_called_once()
        assert abs(usage_mb - 80.0) < 0.01


# ── Feature flag disabled ─────────────────────────────────────────────────

class TestFeatureFlag:
    def test_disabled_always_allows(self):
        mgr = make_manager(enabled=False)
        usage_mb, allowed = mgr.check_quota(HOSTED_ID)
        assert allowed is True
        assert usage_mb == 0.0

    def test_disabled_is_enabled_returns_false(self):
        mgr = make_manager(enabled=False)
        assert mgr.is_enabled() is False


# ── Checkpoint bypass ─────────────────────────────────────────────────────

class TestCheckpointBypass:
    @pytest.mark.parametrize("path", list(CHECKPOINT_BYPASS_PREFIXES) + [
        "checkpoints/turn_42.json",
        "checkpoints/subdir/file",
    ])
    def test_checkpoint_paths_are_bypassed(self, path: str):
        mgr = make_manager()
        assert mgr.is_checkpoint_path(path) is True

    @pytest.mark.parametrize("path", [
        "main.py",
        "README.md",
        "memory/MEMORY.md",
        "src/app.py",
        ".gitignore",
    ])
    def test_normal_paths_are_not_bypassed(self, path: str):
        mgr = make_manager()
        assert mgr.is_checkpoint_path(path) is False


# ── cache invalidation ────────────────────────────────────────────────────

class TestCacheInvalidation:
    def test_invalidate_removes_entry(self):
        mgr = make_manager()
        mgr._cache[HOSTED_ID] = (100 * 1024 * 1024, time.monotonic())
        mgr.invalidate(HOSTED_ID)
        assert mgr.get_cached_usage_mb(HOSTED_ID) is None

    def test_invalidate_nonexistent_is_noop(self):
        mgr = make_manager()
        mgr.invalidate("ghost-id")  # must not raise


# ── watcher_loop (async smoke) ────────────────────────────────────────────

class TestWatcherLoop:
    @pytest.mark.asyncio
    async def test_soft_breach_triggers_callback(self):
        mgr = make_manager(soft_mb=50, hard_mb=200)
        # 100 MB — above soft, below hard
        mgr.measure_usage_mb = MagicMock(return_value=100.0)
        mgr.handle_soft_breach = AsyncMock()
        mgr._emit_event = AsyncMock()

        task = asyncio.create_task(mgr.watcher_loop(HOSTED_ID, interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mgr.handle_soft_breach.assert_called()

    @pytest.mark.asyncio
    async def test_hard_breach_emits_event(self):
        mgr = make_manager(soft_mb=50, hard_mb=100)
        # 150 MB — above hard
        mgr.measure_usage_mb = MagicMock(return_value=150.0)
        mgr._emit_event = AsyncMock()

        task = asyncio.create_task(mgr.watcher_loop(HOSTED_ID, interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        calls = [c.args[1] for c in mgr._emit_event.call_args_list]
        assert "quota_hard_exceeded" in calls

    @pytest.mark.asyncio
    async def test_disabled_watcher_does_not_emit(self):
        mgr = make_manager(soft_mb=10, hard_mb=20, enabled=False)
        mgr._emit_event = AsyncMock()

        task = asyncio.create_task(mgr.watcher_loop(HOSTED_ID, interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mgr._emit_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_hard_breach_warns_only_once_per_cycle(self):
        """Hard breach should emit exactly once until usage drops back."""
        mgr = make_manager(soft_mb=50, hard_mb=100)
        mgr.measure_usage_mb = MagicMock(return_value=150.0)
        mgr._emit_event = AsyncMock()

        task = asyncio.create_task(mgr.watcher_loop(HOSTED_ID, interval=0.01))
        await asyncio.sleep(0.08)  # several cycles
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        hard_calls = [c for c in mgr._emit_event.call_args_list if c.args[1] == "quota_hard_exceeded"]
        assert len(hard_calls) == 1, "should emit hard_exceeded exactly once per sustained breach"

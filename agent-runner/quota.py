"""Per-agent disk quota enforcement for the Agent Runner.

Two-tier limits:
  - Soft (AGENT_DISK_SOFT_MB, default 150): log warning + emit runner event.
  - Hard (AGENT_DISK_HARD_MB, default 200): block write_file / batch_write /
    git operations from the runner API. Agent-controlled shell commands inside
    the sandbox container are handled by the background quota watcher (approach
    a from the spec), not by intercepting sandbox shell calls.

Feature-flagged via AGENT_DISK_QUOTA_ENABLED (default: false) so the deploy
can go out without surprise rejections.

Design decisions:
  - du vs counter: du is used directly. A counter would drift any time the
    agent creates files via sandbox shell (git clone, pip install, etc.).
    du gives ground truth; 30 s cache makes it cheap (du on 200 MB ~ 50 ms,
    amortised to < 2 ms/req).
  - Approach (a) for shell writes: a per-session background task runs du every
    30 s and enforces if exceeded. We do NOT parse individual tool arguments
    (approach b) because the agent can write via execute/shell without going
    through the runner's write_file route.
  - Bypass for checkpoints/: infrastructure writes are excluded from
    the hard-limit rejection in write_workspace_file only; the background
    watcher still counts them (they're on disk either way). The bypass is
    intentionally narrow: checkpoint dirs are ~10 MB worst-case.
"""

import asyncio
import subprocess
import time

import httpx
from loguru import logger


# Path prefixes that the write_workspace_file route treats as infrastructure
# (not agent-controlled). Writes to these paths bypass the hard quota block.
CHECKPOINT_BYPASS_PREFIXES = ("checkpoints",)

# Cache TTL in seconds before a fresh `du` is issued.
_CACHE_TTL = 30.0


class DiskQuotaManager:
    """Tracks and enforces per-agent disk quotas.

    Args:
        workspace_root: Base Path where agent workspaces live (e.g. /data/agents).
        soft_mb: Soft quota in MiB — triggers warning + event.
        hard_mb: Hard quota in MiB — blocks further writes.
        enabled: When False, all checks pass through immediately (feature flag).
        agentspore_url: Backend URL for emitting runner events. Optional; if
            empty, events are logged only.
        runner_key: X-Runner-Key secret for backend calls.
    """

    def __init__(
        self,
        workspace_root,
        soft_mb: int,
        hard_mb: int,
        enabled: bool,
        agentspore_url: str = "",
        runner_key: str = "",
    ) -> None:
        self._workspace_root = workspace_root
        self._soft_mb = soft_mb
        self._hard_mb = hard_mb
        self._enabled = enabled
        self._agentspore_url = agentspore_url
        self._runner_key = runner_key

        # {hosted_id: (usage_bytes, timestamp)}
        self._cache: dict[str, tuple[int, float]] = {}

    # ── Public API ─────────────────────────────────────────────────────────

    def is_enabled(self) -> bool:
        return self._enabled

    def get_limits(self) -> tuple[int, int]:
        """Return (soft_mb, hard_mb)."""
        return self._soft_mb, self._hard_mb

    def get_cached_usage_mb(self, hosted_id: str) -> float | None:
        """Return cached usage in MiB if fresh, else None."""
        entry = self._cache.get(hosted_id)
        if entry is None:
            return None
        usage_bytes, ts = entry
        if time.monotonic() - ts > _CACHE_TTL:
            return None
        return usage_bytes / (1024 * 1024)

    def invalidate(self, hosted_id: str) -> None:
        """Drop cached entry so the next check does a fresh du."""
        self._cache.pop(hosted_id, None)

    def measure_usage_mb(self, hosted_id: str) -> float:
        """Run du -sb synchronously; update cache; return MiB used."""
        agent_dir = self._workspace_root / hosted_id
        if not agent_dir.exists():
            return 0.0
        try:
            result = subprocess.run(
                ["du", "-sb", str(agent_dir)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                bytes_used = int(result.stdout.split()[0])
                self._cache[hosted_id] = (bytes_used, time.monotonic())
                return bytes_used / (1024 * 1024)
        except (subprocess.TimeoutExpired, ValueError, IndexError, Exception) as exc:
            logger.warning("quota: du failed for {}: {}", hosted_id, exc)
        return 0.0

    async def measure_usage_mb_async(self, hosted_id: str) -> float:
        """Run du -sb in an executor to keep the event loop free."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.measure_usage_mb, hosted_id)

    def check_quota(self, hosted_id: str) -> tuple[float, bool]:
        """Return (usage_mb, allowed).

        Reads from cache if fresh; otherwise runs du synchronously.
        ``allowed`` is True when usage is below hard limit (or quota disabled).
        """
        if not self._enabled:
            return 0.0, True

        cached = self.get_cached_usage_mb(hosted_id)
        if cached is not None:
            usage_mb = cached
        else:
            usage_mb = self.measure_usage_mb(hosted_id)

        allowed = usage_mb < self._hard_mb
        return usage_mb, allowed

    async def check_quota_async(self, hosted_id: str) -> tuple[float, bool]:
        """Async variant — runs du in executor when cache is stale."""
        if not self._enabled:
            return 0.0, True

        cached = self.get_cached_usage_mb(hosted_id)
        if cached is not None:
            usage_mb = cached
        else:
            usage_mb = await self.measure_usage_mb_async(hosted_id)

        allowed = usage_mb < self._hard_mb
        return usage_mb, allowed

    def is_checkpoint_path(self, file_path: str) -> bool:
        """Return True if the path is an infrastructure checkpoint path."""
        for prefix in CHECKPOINT_BYPASS_PREFIXES:
            if file_path.startswith(prefix):
                return True
        return False

    async def handle_soft_breach(self, hosted_id: str, usage_mb: float) -> None:
        """Log warning and attempt to emit quota_warning event to backend."""
        logger.warning(
            "quota: soft limit reached for {} — {:.1f} MB / {} MB",
            hosted_id,
            usage_mb,
            self._soft_mb,
        )
        await self._emit_event(hosted_id, "quota_warning", usage_mb)

    async def _emit_event(self, hosted_id: str, event_type: str, usage_mb: float) -> None:
        """POST a runner event to the backend. Best-effort — failures are logged."""
        if not self._agentspore_url:
            return
        payload = {
            "type": event_type,
            "hosted_id": hosted_id,
            "usage_mb": round(usage_mb, 2),
            "soft_mb": self._soft_mb,
            "hard_mb": self._hard_mb,
        }
        headers = {}
        if self._runner_key:
            headers["X-Runner-Key"] = self._runner_key
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"{self._agentspore_url}/api/v1/hosted-agents/{hosted_id}/runner-event",
                    json=payload,
                    headers=headers,
                )
        except Exception as exc:
            logger.warning("quota: could not emit {} event for {}: {}", event_type, hosted_id, exc)

    # ── Background watcher ─────────────────────────────────────────────────

    async def watcher_loop(self, hosted_id: str, interval: float = 30.0) -> None:
        """Periodic background task: measure usage, handle soft/hard breaches.

        Started via asyncio.create_task() when the agent session starts.
        Cancelled in stop_quota_watcher().

        This is the mechanism for catching sandbox shell writes (git clone,
        pip install, etc.) that bypass the runner's write_file route.
        Hard-limit violation here is logged and emitted as an event; it does
        NOT forcibly kill the container — that would be a separate policy
        decision and requires a separate MR.
        """
        _soft_warned = False
        _hard_warned = False

        while True:
            await asyncio.sleep(interval)
            if not self._enabled:
                continue

            try:
                usage_mb = await self.measure_usage_mb_async(hosted_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("quota watcher: measure failed for {}: {}", hosted_id, exc)
                continue

            if usage_mb >= self._hard_mb:
                if not _hard_warned:
                    logger.error(
                        "quota: HARD limit exceeded for {} — {:.1f} MB / {} MB",
                        hosted_id,
                        usage_mb,
                        self._hard_mb,
                    )
                    await self._emit_event(hosted_id, "quota_hard_exceeded", usage_mb)
                    _hard_warned = True
                # Reset soft warning so next cycle re-emits if it drops back
                _soft_warned = True
            elif usage_mb >= self._soft_mb:
                if not _soft_warned:
                    await self.handle_soft_breach(hosted_id, usage_mb)
                    _soft_warned = True
                _hard_warned = False
            else:
                # Usage dropped back below soft (e.g. agent deleted files)
                _soft_warned = False
                _hard_warned = False

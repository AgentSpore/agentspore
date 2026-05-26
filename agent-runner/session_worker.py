"""Per-session worker: isolated message history, lock, and directory paths.

Each SessionWorker encapsulates the runtime state for one owner session
(platform session_id). Multiple SessionWorkers share the same DeepAgent
instance (which is stateless between runs — history is passed explicitly)
and the same Docker sandbox, but maintain independent:
  - message_history (conversation state)
  - asyncio.Lock (within-session serialization)
  - memory_dir and checkpoint_dir paths under /workspace/sessions/<session_id>/
  - last_active timestamp (LRU eviction)

The WorkerPool controls cross-session concurrency via an asyncio.Semaphore
and routes queued messages when all workers are busy.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from loguru import logger

if TYPE_CHECKING:
    pass


@dataclass
class SessionWorker:
    """Runtime state for a single owner session within a hosted agent."""

    session_id: str
    memory_dir: str
    checkpoint_dir: str
    message_history: list = field(default_factory=list)
    last_active: float = field(default_factory=time.time)
    # Per-session lock: serializes messages within this session
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Current status for /status reporting
    status: str = "idle"  # "running" | "queued" | "idle"
    # Queue depth = number of messages waiting for this session's lock
    queue_depth: int = 0

    def touch(self) -> None:
        """Update last active timestamp."""
        self.last_active = time.time()

    def is_idle(self, ttl_seconds: int) -> bool:
        """Return True if worker has been idle longer than ttl_seconds."""
        return (time.time() - self.last_active) > ttl_seconds

    @property
    def last_active_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.last_active, tz=timezone.utc).isoformat()


class WorkerPool:
    """Manages per-session workers + cross-session concurrency for one hosted agent.

    Architecture:
      - session_workers: dict[str, SessionWorker] — keyed by session_id
      - executor_semaphore: asyncio.Semaphore(max_concurrent) — limits parallel LLM calls
      - llm_semaphore: asyncio.Semaphore(max_llm_concurrent) — prevents upstream 429s
      - LRU eviction background task: unloads workers idle > session_ttl_seconds

    Within-session messages are serialized by SessionWorker.lock.
    Across sessions: up to max_concurrent workers run in parallel.
    When all workers busy, new messages queue on the session lock (FIFO per session).
    """

    def __init__(
        self,
        hosted_id: str,
        workspace_root: Path,
        max_concurrent: int = 1,
        max_llm_concurrent: int = 3,
        max_session_instances: int = 20,
        session_ttl_seconds: int = 1800,
    ) -> None:
        self.hosted_id = hosted_id
        self.workspace_root = workspace_root
        self.max_concurrent = max_concurrent
        self.max_llm_concurrent = max_llm_concurrent
        self.max_session_instances = max_session_instances
        self.session_ttl_seconds = session_ttl_seconds

        # Cross-session concurrency gate
        self.executor_semaphore = asyncio.Semaphore(max_concurrent)
        # LLM-level rate guard (shared across sessions of this agent)
        self.llm_semaphore = asyncio.Semaphore(max_llm_concurrent)

        self._workers: dict[str, SessionWorker] = {}
        self._lock = asyncio.Lock()  # guards _workers dict mutations
        self._eviction_task: asyncio.Task | None = None

    # ── Worker lifecycle ────────────────────────────────────────────────────

    async def get_or_create_worker(self, session_id: str) -> SessionWorker:
        """Return existing worker for session_id or create a new one (LRU-evict if needed)."""
        async with self._lock:
            if session_id in self._workers:
                return self._workers[session_id]

            # Evict oldest idle worker if at capacity
            if len(self._workers) >= self.max_session_instances:
                await self._evict_one_idle()

            worker = self._create_worker(session_id)
            self._workers[session_id] = worker
            logger.info(
                "WorkerPool[{}]: created session worker {} ({} total)",
                self.hosted_id, session_id, len(self._workers),
            )
            return worker

    def _create_worker(self, session_id: str) -> SessionWorker:
        """Instantiate SessionWorker with isolated dirs under workspace/sessions/."""
        session_dir = self.workspace_root / self.hosted_id / "sessions" / session_id
        memory_dir = str(session_dir / "memory")
        checkpoint_dir = str(session_dir / ".deep" / "checkpoints")
        # Dirs created lazily by agent toolsets; no mkdir here to avoid blocking IO
        return SessionWorker(
            session_id=session_id,
            memory_dir=memory_dir,
            checkpoint_dir=checkpoint_dir,
        )

    async def _evict_one_idle(self) -> None:
        """Evict the least-recently-active idle worker (must hold self._lock)."""
        idle = [
            (sid, w) for sid, w in self._workers.items()
            if not w.lock.locked()
        ]
        if not idle:
            logger.warning(
                "WorkerPool[{}]: all {} workers busy, cannot evict",
                self.hosted_id, len(self._workers),
            )
            return
        oldest_sid = min(idle, key=lambda x: x[1].last_active)[0]
        del self._workers[oldest_sid]
        logger.info(
            "WorkerPool[{}]: evicted idle session {} (LRU, capacity {})",
            self.hosted_id, oldest_sid, self.max_session_instances,
        )

    async def evict_stale_workers(self) -> int:
        """Remove all workers idle longer than session_ttl_seconds. Returns evict count."""
        async with self._lock:
            stale = [
                sid for sid, w in self._workers.items()
                if not w.lock.locked() and w.is_idle(self.session_ttl_seconds)
            ]
            for sid in stale:
                del self._workers[sid]
            if stale:
                logger.info(
                    "WorkerPool[{}]: TTL-evicted {} session(s): {}",
                    self.hosted_id, len(stale), stale,
                )
            return len(stale)

    # ── Concurrency context manager ─────────────────────────────────────────

    async def acquire_slot(self, session_id: str) -> SessionWorker:
        """Get-or-create worker, enqueue if pool full, return worker with slot reserved.

        Caller must release via release_slot(session_id) in a finally block.
        The session lock is NOT acquired here — callers must acquire worker.lock
        themselves to serialize within-session messages.
        """
        worker = await self.get_or_create_worker(session_id)
        worker.touch()

        # Track queue depth for /status reporting
        if self.executor_semaphore._value == 0:  # type: ignore[attr-defined]
            worker.queue_depth += 1
            worker.status = "queued"
            logger.debug(
                "WorkerPool[{}]: session {} queued (pool full, {} busy)",
                self.hosted_id, session_id, self.max_concurrent,
            )

        await self.executor_semaphore.acquire()

        worker.queue_depth = max(0, worker.queue_depth - 1)
        worker.status = "running"
        return worker

    def release_slot(self, worker: SessionWorker) -> None:
        """Release executor semaphore slot and reset worker status."""
        worker.status = "idle"
        self.executor_semaphore.release()

    # ── Status reporting ────────────────────────────────────────────────────

    def status_snapshot(self) -> dict:
        """Return pool status dict for /status endpoint."""
        busy_count = self.max_concurrent - self.executor_semaphore._value  # type: ignore[attr-defined]
        sessions_info = [
            {
                "session_id": sid,
                "status": w.status,
                "queue_depth": w.queue_depth,
                "last_active": w.last_active_iso,
            }
            for sid, w in self._workers.items()
        ]
        return {
            "worker_pool": {
                "total": self.max_concurrent,
                "busy": busy_count,
                "available": max(0, self.max_concurrent - busy_count),
            },
            "sessions": sessions_info,
        }

    # ── Background eviction ─────────────────────────────────────────────────

    def start_eviction_task(self) -> None:
        """Start background LRU eviction loop (call once on agent start)."""
        if self._eviction_task is None or self._eviction_task.done():
            self._eviction_task = asyncio.create_task(self._eviction_loop())

    def stop_eviction_task(self) -> None:
        """Cancel background eviction loop (call on agent stop)."""
        if self._eviction_task:
            self._eviction_task.cancel()
            self._eviction_task = None

    async def _eviction_loop(self) -> None:
        """Periodically evict stale session workers (every 5 minutes)."""
        while True:
            try:
                await asyncio.sleep(300)
                await self.evict_stale_workers()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("WorkerPool[{}]: eviction loop error: {}", self.hosted_id, e)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def get_worker(self, session_id: str) -> SessionWorker | None:
        """Return existing worker or None (non-blocking, no creation)."""
        return self._workers.get(session_id)

    @property
    def worker_count(self) -> int:
        return len(self._workers)

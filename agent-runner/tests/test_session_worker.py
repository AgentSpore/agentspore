"""Tests for SessionWorker and WorkerPool concurrency primitives.

Covers:
  - test_two_sessions_run_concurrently
  - test_three_sessions_full_pool_queues_fourth
  - test_session_eviction_after_idle
  - test_within_session_msgs_serialized
  - test_startup_done_flag_emits_true_after_init
  - test_status_endpoint_returns_worker_pool_fields
  - test_legacy_path_single_session_no_regression
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure runner root is on path (mirrors conftest.py)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_worker import SessionWorker, WorkerPool  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def pool(tmp_workspace: Path) -> WorkerPool:
    return WorkerPool(
        hosted_id="test-agent",
        workspace_root=tmp_workspace,
        max_concurrent=3,
        max_llm_concurrent=3,
        max_session_instances=20,
        session_ttl_seconds=1800,
    )


# ── Unit: SessionWorker ───────────────────────────────────────────────────────

class TestSessionWorker:
    def test_initial_state(self):
        w = SessionWorker(session_id="s1", memory_dir="/mem", checkpoint_dir="/ckpt")
        assert w.status == "idle"
        assert w.queue_depth == 0
        assert w.message_history == []
        assert not w.lock.locked()

    def test_touch_updates_last_active(self):
        w = SessionWorker(session_id="s1", memory_dir="/m", checkpoint_dir="/c")
        before = w.last_active
        time.sleep(0.01)
        w.touch()
        assert w.last_active > before

    def test_is_idle_respects_ttl(self):
        w = SessionWorker(session_id="s1", memory_dir="/m", checkpoint_dir="/c")
        w.last_active = time.time() - 3700
        assert w.is_idle(3600)

    def test_is_not_idle_within_ttl(self):
        w = SessionWorker(session_id="s1", memory_dir="/m", checkpoint_dir="/c")
        assert not w.is_idle(3600)

    def test_last_active_iso_format(self):
        w = SessionWorker(session_id="s1", memory_dir="/m", checkpoint_dir="/c")
        iso = w.last_active_iso
        assert "T" in iso and "Z" in iso or "+" in iso  # ISO-8601 UTC


# ── Unit: WorkerPool ──────────────────────────────────────────────────────────

class TestWorkerPool:
    @pytest.mark.asyncio
    async def test_get_or_create_returns_same_worker(self, pool: WorkerPool):
        w1 = await pool.get_or_create_worker("session-A")
        w2 = await pool.get_or_create_worker("session-A")
        assert w1 is w2

    @pytest.mark.asyncio
    async def test_different_sessions_get_different_workers(self, pool: WorkerPool):
        w1 = await pool.get_or_create_worker("session-A")
        w2 = await pool.get_or_create_worker("session-B")
        assert w1 is not w2
        assert w1.message_history is not w2.message_history

    @pytest.mark.asyncio
    async def test_lru_eviction_at_capacity(self, tmp_workspace: Path):
        """When max_session_instances reached, oldest idle worker is evicted."""
        pool = WorkerPool(
            hosted_id="evict-test",
            workspace_root=tmp_workspace,
            max_concurrent=5,
            max_session_instances=3,
        )
        w1 = await pool.get_or_create_worker("s1")
        w1.last_active = time.time() - 1000  # oldest
        w2 = await pool.get_or_create_worker("s2")
        w3 = await pool.get_or_create_worker("s3")
        assert pool.worker_count == 3

        # Creating s4 should evict s1 (oldest)
        await pool.get_or_create_worker("s4")
        assert pool.worker_count == 3
        assert pool.get_worker("s1") is None
        assert pool.get_worker("s4") is not None

    @pytest.mark.asyncio
    async def test_evict_stale_workers(self, pool: WorkerPool):
        w = await pool.get_or_create_worker("stale-session")
        w.last_active = time.time() - 9999  # way past TTL
        count = await pool.evict_stale_workers()
        assert count == 1
        assert pool.get_worker("stale-session") is None

    @pytest.mark.asyncio
    async def test_locked_worker_not_evicted(self, pool: WorkerPool):
        """A worker with its lock held (mid-run) must NOT be evicted."""
        w = await pool.get_or_create_worker("active-session")
        w.last_active = time.time() - 9999
        await w.lock.acquire()  # simulate in-flight run
        try:
            count = await pool.evict_stale_workers()
            assert count == 0
            assert pool.get_worker("active-session") is w
        finally:
            w.lock.release()

    @pytest.mark.asyncio
    async def test_status_snapshot_structure(self, pool: WorkerPool):
        await pool.get_or_create_worker("s1")
        snap = pool.status_snapshot()
        assert "worker_pool" in snap
        assert "sessions" in snap
        wp = snap["worker_pool"]
        assert wp["total"] == 3
        assert wp["busy"] == 0
        assert wp["available"] == 3
        assert len(snap["sessions"]) == 1
        sess = snap["sessions"][0]
        assert sess["session_id"] == "s1"
        assert "status" in sess
        assert "queue_depth" in sess
        assert "last_active" in sess

    @pytest.mark.asyncio
    async def test_acquire_release_slot(self, pool: WorkerPool):
        worker = await pool.acquire_slot("s1")
        snap = pool.status_snapshot()
        assert snap["worker_pool"]["busy"] == 1
        pool.release_slot(worker)
        snap2 = pool.status_snapshot()
        assert snap2["worker_pool"]["busy"] == 0
        assert worker.status == "idle"


# ── Integration: concurrency behavior ────────────────────────────────────────

class TestConcurrency:
    @pytest.mark.asyncio
    async def test_two_sessions_run_concurrently(self, pool: WorkerPool):
        """Two sessions in a pool(max=3) must run in parallel, not block each other."""
        started: list[str] = []
        finished: list[str] = []
        barrier = asyncio.Event()

        async def run_session(sid: str):
            worker = await pool.acquire_slot(sid)
            async with worker.lock:
                started.append(sid)
                await barrier.wait()  # both reach here before either continues
                finished.append(sid)
            pool.release_slot(worker)

        t1 = asyncio.create_task(run_session("s1"))
        t2 = asyncio.create_task(run_session("s2"))

        # Let both tasks start and block on barrier
        await asyncio.sleep(0.05)
        assert len(started) == 2, "Both sessions must start concurrently"

        barrier.set()
        await asyncio.gather(t1, t2)
        assert set(finished) == {"s1", "s2"}

    @pytest.mark.asyncio
    async def test_three_sessions_full_pool_queues_fourth(self, tmp_workspace: Path):
        """Pool(max=3): sessions 1-3 occupy all slots; session 4 queues."""
        pool = WorkerPool(
            hosted_id="queue-test",
            workspace_root=tmp_workspace,
            max_concurrent=3,
        )
        release_events: dict[str, asyncio.Event] = {f"s{i}": asyncio.Event() for i in range(1, 5)}
        acquired_order: list[str] = []

        async def hold_slot(sid: str):
            worker = await pool.acquire_slot(sid)
            acquired_order.append(sid)
            async with worker.lock:
                await release_events[sid].wait()
            pool.release_slot(worker)

        # Start 3 sessions — all should acquire immediately
        tasks = [asyncio.create_task(hold_slot(f"s{i}")) for i in range(1, 4)]
        await asyncio.sleep(0.05)
        assert len(acquired_order) == 3

        # 4th session — pool full, must queue
        t4 = asyncio.create_task(hold_slot("s4"))
        await asyncio.sleep(0.05)
        assert "s4" not in acquired_order, "s4 must be blocked (pool full)"

        # Release s1 — s4 should now run
        release_events["s1"].set()
        await asyncio.sleep(0.05)
        assert "s4" in acquired_order, "s4 must start after s1 releases"

        # Cleanup
        for key in ("s2", "s3", "s4"):
            release_events[key].set()
        await asyncio.gather(*tasks, t4)

    @pytest.mark.asyncio
    async def test_within_session_msgs_serialized(self, pool: WorkerPool):
        """Two messages to the same session must be serialized (not overlap)."""
        executed: list[tuple[str, float]] = []

        async def send_msg(sid: str, msg_id: str):
            worker = await pool.acquire_slot(sid)
            async with worker.lock:
                start = time.monotonic()
                await asyncio.sleep(0.03)
                end = time.monotonic()
                executed.append((msg_id, start, end))
            pool.release_slot(worker)

        t1 = asyncio.create_task(send_msg("s1", "msg1"))
        t2 = asyncio.create_task(send_msg("s1", "msg2"))  # same session
        await asyncio.gather(t1, t2)

        # Verify no temporal overlap between msg1 and msg2
        assert len(executed) == 2
        (id_a, start_a, end_a) = executed[0]
        (id_b, start_b, end_b) = executed[1]
        # One must finish before the other starts
        assert end_a <= start_b or end_b <= start_a, (
            f"Messages overlapped: {id_a}=[{start_a:.3f},{end_a:.3f}] "
            f"{id_b}=[{start_b:.3f},{end_b:.3f}]"
        )


# ── Integration: FastAPI endpoint smoke tests ────────────────────────────────

class TestStatusEndpoint:
    @pytest.mark.asyncio
    async def test_status_endpoint_returns_worker_pool_fields(self):
        """Smoke: /status returns worker_pool and sessions fields."""
        import main  # noqa: E402 — top-level import needed after sys.path set

        # Build a minimal fake session with worker_pool
        fake_pool = MagicMock()
        fake_pool.max_concurrent = 2
        fake_pool.status_snapshot.return_value = {
            "worker_pool": {"total": 2, "busy": 0, "available": 2},
            "sessions": [],
        }

        fake_session = SimpleNamespace(
            sandbox=None,
            chat_lock=asyncio.Lock(),
            active_session_id=None,
            bootstrap_done=True,
            worker_pool=fake_pool,
            stop_heartbeat=lambda: None,
            stop_websocket=lambda: None,
        )

        from fastapi.testclient import TestClient
        with patch.dict(main.sessions, {"test-agent": fake_session}):
            client = TestClient(main.app)
            resp = client.get(
                "/agents/test-agent/status",
                headers={"X-Runner-Key": "test-runner-key"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "worker_pool" in data
        assert "sessions" in data
        assert data["worker_pool"]["total"] == 2
        # Backward-compat fields still present
        assert "busy" in data
        assert "startup_done" in data

    @pytest.mark.asyncio
    async def test_status_stopped_agent_has_default_pool(self):
        """Stopped agent returns worker_pool default shape."""
        import main  # noqa: E402

        from fastapi.testclient import TestClient
        with patch.dict(main.sessions, {}, clear=True):
            client = TestClient(main.app)
            resp = client.get(
                "/agents/nonexistent/status",
                headers={"X-Runner-Key": "test-runner-key"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stopped"
        assert data["worker_pool"] == {"total": 1, "busy": 0, "available": 1}
        assert data["sessions"] == []

    def test_startup_done_flag_emits_true_after_init(self):
        """bootstrap_done is False before first chat, True after."""
        import main  # noqa: E402

        fake_pool = MagicMock()
        fake_pool.max_concurrent = 1
        fake_pool.status_snapshot.return_value = {
            "worker_pool": {"total": 1, "busy": 0, "available": 1},
            "sessions": [],
        }

        for bootstrap_done_val in (False, True):
            fake_session = SimpleNamespace(
                sandbox=None,
                chat_lock=asyncio.Lock(),
                active_session_id=None,
                bootstrap_done=bootstrap_done_val,
                worker_pool=fake_pool,
                stop_heartbeat=lambda: None,
                stop_websocket=lambda: None,
            )

            from fastapi.testclient import TestClient
            with patch.dict(main.sessions, {"agent-x": fake_session}):
                client = TestClient(main.app)
                resp = client.get(
                    "/agents/agent-x/status",
                    headers={"X-Runner-Key": "test-runner-key"},
                )
            assert resp.status_code == 200
            assert resp.json()["startup_done"] == bootstrap_done_val


class TestLegacyPath:
    def test_legacy_path_single_session_no_regression(self):
        """max_concurrent=1 (default): chat falls back to global lock path."""
        import main  # noqa: E402

        # Build a fake session that mimics AgentSession but with a mock agent
        fake_result = SimpleNamespace(
            output="Hello",
            all_messages=lambda: [],
        )
        fake_agent = MagicMock()
        fake_agent.run = AsyncMock(return_value=fake_result)

        # Patch extract_response to avoid real pydantic-ai parsing
        with patch("routes.chat._extract_response", return_value=("Hello", [], None)):
            with patch("routes.chat.sanitize_history", return_value=[]):
                fake_pool = MagicMock()
                fake_pool.max_concurrent = 1  # legacy mode

                fake_session = SimpleNamespace(
                    agent=fake_agent,
                    deps=None,
                    message_history=[],
                    chat_lock=asyncio.Lock(),
                    active_session_id=None,
                    bootstrap_done=False,
                    agent_handle="test",
                    model="test-model",
                    worker_pool=fake_pool,
                    touch=lambda: None,
                )
                fake_session.bootstrap_done = False

                from fastapi.testclient import TestClient
                with patch.dict(main.sessions, {"agent-legacy": fake_session}):
                    client = TestClient(main.app)
                    resp = client.post(
                        "/agents/agent-legacy/chat",
                        json={"content": "Hello", "owner_session_id": "sess-1"},
                        headers={"X-Runner-Key": "test-runner-key"},
                    )
                # 200 = legacy path executed successfully
                assert resp.status_code == 200
                assert resp.json()["reply"] == "Hello"
                # bootstrap_done was flipped
                assert fake_session.bootstrap_done is True

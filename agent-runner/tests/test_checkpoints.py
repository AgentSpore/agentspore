"""Tests for the runner-side checkpoint endpoints.

Regression coverage for the checkpoint store discovery logic. Earlier
versions looked for ``ts.checkpoint_store`` on iterable toolsets, but
pydantic-deep keeps the reference under ``_fallback_store`` on
``CheckpointToolset`` and exposes the store API as async (``list_all``,
``get``). The runner endpoint is the only thing the FastAPI app
exposes for owners to drive a rewind, so it has to keep tracking the
upstream contract.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402


def _fake_checkpoint(cp_id: str, turn: int, label: str | None = None):
    return SimpleNamespace(
        id=cp_id,
        label=label or f"turn-{turn}",
        turn=turn,
        message_count=turn * 2,
        created_at=datetime.now(timezone.utc),
        messages=[{"kind": "request", "parts": []}] * (turn * 2),
    )


def _fake_session_with_toolset_store(checkpoints):
    """Mimic the pydantic-deep wiring: store on CheckpointToolset._fallback_store."""
    store = MagicMock()
    store.list_all = AsyncMock(return_value=checkpoints)
    store.get = AsyncMock(side_effect=lambda cp_id: next(
        (cp for cp in checkpoints if cp.id == cp_id), None
    ))

    toolset = SimpleNamespace(_fallback_store=store)
    other_toolset = SimpleNamespace()  # no store attribute
    agent = SimpleNamespace(toolsets=[other_toolset, toolset])
    deps = SimpleNamespace(checkpoint_store=None)

    return SimpleNamespace(agent=agent, deps=deps, message_history=[])


def _fake_session_with_deps_store(checkpoints):
    """Mimic the alternate wiring: store injected via deps."""
    store = MagicMock()
    store.list_all = AsyncMock(return_value=checkpoints)
    store.get = AsyncMock(side_effect=lambda cp_id: next(
        (cp for cp in checkpoints if cp.id == cp_id), None
    ))

    agent = SimpleNamespace(toolsets=[])
    deps = SimpleNamespace(checkpoint_store=store)
    return SimpleNamespace(agent=agent, deps=deps, message_history=[])


def _fake_session_no_store():
    return SimpleNamespace(
        agent=SimpleNamespace(toolsets=[SimpleNamespace()]),
        deps=SimpleNamespace(checkpoint_store=None),
        message_history=[],
    )


@pytest.fixture
def client():
    return TestClient(main.app)


def test_resolve_store_prefers_deps_over_toolset():
    cp = [_fake_checkpoint("dep-1", 1)]
    session = _fake_session_with_deps_store(cp)
    found = main._resolve_checkpoint_store(session)
    assert found is session.deps.checkpoint_store


def test_resolve_store_falls_back_to_toolset_fallback_store():
    cp = [_fake_checkpoint("fb-1", 1)]
    session = _fake_session_with_toolset_store(cp)
    found = main._resolve_checkpoint_store(session)
    # Should be the store hidden under _fallback_store on the toolset
    expected = session.agent.toolsets[1]._fallback_store
    assert found is expected


def test_resolve_store_returns_none_when_no_store():
    session = _fake_session_no_store()
    assert main._resolve_checkpoint_store(session) is None


def test_list_checkpoints_returns_serialised_payload(client):
    hosted_id = "agent-with-cps"
    checkpoints = [
        _fake_checkpoint("cp-1", 1),
        _fake_checkpoint("cp-2", 2),
    ]
    main.sessions[hosted_id] = _fake_session_with_toolset_store(checkpoints)
    try:
        resp = client.get(f"/agents/{hosted_id}/checkpoints")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["checkpoints"]) == 2
        # created_at is serialised to ISO string for JSON consumers
        assert isinstance(data["checkpoints"][0]["created_at"], str)
        assert data["checkpoints"][0]["id"] == "cp-1"
        assert data["checkpoints"][1]["turn"] == 2
        assert data["checkpoints"][1]["message_count"] == 4
    finally:
        main.sessions.pop(hosted_id, None)


def test_list_checkpoints_empty_when_session_lacks_store(client):
    hosted_id = "agent-no-store"
    main.sessions[hosted_id] = _fake_session_no_store()
    try:
        resp = client.get(f"/agents/{hosted_id}/checkpoints")
        assert resp.status_code == 200
        assert resp.json() == {"checkpoints": []}
    finally:
        main.sessions.pop(hosted_id, None)


def test_list_checkpoints_unknown_session_returns_404(client):
    resp = client.get("/agents/not-running/checkpoints")
    assert resp.status_code == 404


def test_rewind_restores_messages_and_returns_count(client):
    hosted_id = "agent-rewind"
    cp = _fake_checkpoint("cp-target", 3)
    cp.messages = ["m1", "m2", "m3"]
    session = _fake_session_with_toolset_store([cp])
    session.message_history = ["live-1", "live-2", "live-3", "live-4"]
    main.sessions[hosted_id] = session
    try:
        resp = client.post(
            f"/agents/{hosted_id}/rewind",
            json={"checkpoint_id": "cp-target"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["checkpoint_id"] == "cp-target"
        assert body["message_count"] == 3
        # session.message_history was reset to the snapshot
        assert session.message_history == ["m1", "m2", "m3"]
    finally:
        main.sessions.pop(hosted_id, None)


def test_rewind_unknown_checkpoint_returns_404(client):
    hosted_id = "agent-rewind-miss"
    main.sessions[hosted_id] = _fake_session_with_toolset_store([_fake_checkpoint("cp-x", 1)])
    try:
        resp = client.post(
            f"/agents/{hosted_id}/rewind",
            json={"checkpoint_id": "does-not-exist"},
        )
        assert resp.status_code == 404
    finally:
        main.sessions.pop(hosted_id, None)


def test_rewind_no_store_returns_400(client):
    hosted_id = "agent-no-store-rewind"
    main.sessions[hosted_id] = _fake_session_no_store()
    try:
        resp = client.post(
            f"/agents/{hosted_id}/rewind",
            json={"checkpoint_id": "anything"},
        )
        assert resp.status_code == 400
    finally:
        main.sessions.pop(hosted_id, None)

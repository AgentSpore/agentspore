"""Publication reservations survive process death and never authorize replay."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import agentspore_sdk.operator_controller as storage
import pytest
from agentspore_sdk.publication_state import PublicationState


@pytest.fixture
def intent():
    return {
        "operation_id": str(uuid4()),
        "actor_id": str(uuid4()),
        "step": "create_project",
        "request_digest": "a" * 64,
    }


def test_reservation_restart_and_identity_conflict(tmp_path, intent):
    state = PublicationState(tmp_path / "journal")
    with state.locked() as session:
        assert session.reserve(intent)["disposition"] == "attempt"
    with PublicationState(tmp_path / "journal").locked() as session:
        assert session.reserve(intent)["disposition"] == "readback_required"
        for field, value in [("actor_id", str(uuid4())), ("request_digest", "b" * 64)]:
            with pytest.raises(ValueError, match="conflict"):
                session.reserve({**intent, field: value})
        with pytest.raises(ValueError, match="pending"):
            session.reserve({**intent, "operation_id": str(uuid4())})
    assert (
        "request_digest"
        in json.loads((tmp_path / "journal/publication.json").read_text())["records"][
            0
        ]["intent"]
    )


@pytest.fixture
def result():
    return {
        "project_id": str(uuid4()),
        "repository": "owner/repo",
        "head_sha": None,
        "pr_number": None,
    }


def test_explicit_reconciliation_and_bound_next_step(tmp_path, intent, result):
    state = PublicationState(tmp_path / "state")
    with pytest.raises(RuntimeError, match="session_required"):
        state.reserve(intent)
    with state.locked() as session:
        with pytest.raises(ValueError, match="reservation_required"):
            session.reconcile(intent, result)
        reserved = session.reserve(intent)
        assert reserved["branch"] == "feature/agentspore-" + intent["operation_id"]
        session.reconcile(intent, result)
        assert session.reserve(intent)["disposition"] == "verified"
        changed = {**result, "repository": "owner/other"}
        with pytest.raises(ValueError, match="conflict"):
            session.reconcile(intent, changed)
        with pytest.raises(ValueError, match="conflict"):
            session.reserve({**intent, "step": "create_branch"})
        bound = {
            **intent,
            "step": "create_branch",
            "project_id": result["project_id"],
            "repository": result["repository"],
            "base_sha": "c" * 40,
        }
        assert session.reserve(bound)["disposition"] == "attempt"


@pytest.mark.parametrize(
    "contents",
    [
        b"{",
        b"x" * 65537,
        b'{"version":2,"records":[]}',
        b'{"version":1,"records":[{}]}',
    ],
)
def test_corrupt_state_fails_closed(tmp_path, contents):
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    path = directory / "publication.json"
    path.write_bytes(contents)
    path.chmod(0o600)
    with pytest.raises(ValueError), PublicationState(directory).locked():
        pytest.fail("corrupt journal admitted")


def test_symlink_and_nonprivate_state(tmp_path, intent):
    directory = tmp_path / "state"
    with PublicationState(directory).locked() as session:
        session.reserve(intent)
    path = directory / "publication.json"
    path.chmod(0o644)
    with pytest.raises(PermissionError), PublicationState(directory).locked():
        pytest.fail("public state admitted")
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(OSError), PublicationState(directory).locked():
        pytest.fail("symlink admitted")


@pytest.mark.parametrize(
    "field,value",
    [
        ("content", "body"),
        ("api_key", "SYNTHETIC"),
        ("branch", "main"),
        ("operation_id", "../bad"),
        ("head_sha", "short"),
        ("repository", "https://user:password@example.test/repo"),
    ],
)
def test_closed_metadata_rejected_without_journal(tmp_path, intent, field, value):
    with (
        PublicationState(tmp_path / "state").locked() as session,
        pytest.raises(ValueError),
    ):
        session.reserve({**intent, field: value})
    assert not (tmp_path / "state/publication.json").exists()


def test_real_process_lock_and_crash_restart(tmp_path, intent):
    directory = tmp_path / "state"
    code = """
import json, os, sys
from pathlib import Path
from agentspore_sdk.publication_state import PublicationState
with PublicationState(Path(sys.argv[1])).locked() as session:
    session.reserve(json.loads(sys.argv[2]))
    os._exit(19)
"""
    command = [sys.executable, "-c", code, str(directory), json.dumps(intent)]
    with PublicationState(directory).locked():
        blocked = subprocess.run(command, capture_output=True, timeout=10, check=False)
        assert blocked.returncode != 19
        assert b"publication_busy" in blocked.stderr
    crashed = subprocess.run(command, capture_output=True, timeout=10, check=False)
    assert crashed.returncode == 19, crashed.stderr.decode()
    with PublicationState(directory).locked() as session:
        assert session.reserve(intent)["disposition"] == "readback_required"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "step", ["create_project", "create_branch", "commit_files", "open_draft_pr"]
)
async def test_cancellation_keeps_reservation_and_releases_lock(tmp_path, intent, step):
    intent = {**intent, "step": step}
    reserved = asyncio.Event()
    never = asyncio.Event()
    directory = tmp_path / "state"

    async def caller():
        with PublicationState(directory).locked() as session:
            assert session.reserve(intent)["disposition"] == "attempt"
            reserved.set()
            await never.wait()

    task = asyncio.create_task(caller())
    await asyncio.wait_for(reserved.wait(), timeout=2)
    with (
        pytest.raises(BlockingIOError, match="busy"),
        PublicationState(directory).locked(),
    ):
        pytest.fail("competing caller admitted")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with PublicationState(directory).locked() as session:
        assert session.reserve(intent)["disposition"] == "readback_required"


@pytest.mark.parametrize("failed_call", [1, 2])
def test_fsync_failure_never_grants_attempt(tmp_path, intent, monkeypatch, failed_call):
    real_fsync = os.fsync
    calls = []

    def fail(descriptor):
        calls.append(descriptor)
        if len(calls) == failed_call:
            raise OSError("synthetic fsync failure")
        real_fsync(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "fsync", fail)
        with (
            PublicationState(tmp_path / "state").locked() as session,
            pytest.raises(OSError, match="fsync failure"),
        ):
            session.reserve(intent)
    with PublicationState(tmp_path / "state").locked() as session:
        disposition = session.reserve(intent)["disposition"]
        assert disposition == ("attempt" if failed_call == 1 else "readback_required")


def test_unowned_file_rejected(tmp_path, intent, monkeypatch):
    directory = tmp_path / "state"
    with PublicationState(directory).locked() as session:
        session.reserve(intent)
    real_fstat = os.fstat

    def foreign_owner(descriptor):
        info = real_fstat(descriptor)
        fields = list(info)
        fields[4] = os.getuid() + 1
        return os.stat_result(fields)

    monkeypatch.setattr(storage.os, "fstat", foreign_owner)
    with pytest.raises(PermissionError), PublicationState(directory).locked():
        pytest.fail("foreign-owned file admitted")


def test_directory_symlink_and_permissions(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    real.chmod(0o755)
    for directory in (link, real):
        with pytest.raises(PermissionError), PublicationState(directory).locked():
            pytest.fail("unsafe directory admitted")
    with pytest.raises(ValueError):
        PublicationState(Path("relative"))


def test_bounded_journal_and_no_retry_after_failed_fsync(tmp_path, intent, monkeypatch):
    state = PublicationState(tmp_path / "state")
    accepted = 0
    with state.locked() as session:
        for _ in range(500):
            candidate = {
                **intent,
                "operation_id": str(uuid4()),
                "actor_id": str(uuid4()),
            }
            try:
                session.reserve(candidate)
            except ValueError as error:
                assert "too large" in str(error)
                break
            accepted += 1
        else:
            pytest.fail("journal bound absent")
    assert 1 < accepted < 500
    assert (tmp_path / "state/publication.json").stat().st_size <= 65536
    with PublicationState(tmp_path / "state").locked():
        pass
    with PublicationState(tmp_path / "other").locked() as session:
        with monkeypatch.context() as patch:
            patch.setattr(
                storage.os,
                "fsync",
                lambda _: (_ for _ in ()).throw(OSError("synthetic")),
            )
            with pytest.raises(OSError):
                session.reserve(intent)
        with pytest.raises(RuntimeError, match="session_required"):
            session.reserve(intent)


def test_portable_module_import_has_no_posix_dependency(tmp_path):
    code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in {'fcntl', 'pwd'}:
        raise ImportError('POSIX unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import agentspore_sdk.publication_state
import agentspore_sdk.publication_manifest
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr.decode()


def test_locked_lookup_survives_restart_and_cannot_mutate_journal(tmp_path, intent):
    directory = tmp_path / "state"
    with PublicationState(directory).locked() as session:
        session.reserve(intent)
    with PublicationState(directory).locked() as session:
        snapshot = session.snapshot(intent["operation_id"], intent["actor_id"])
        assert snapshot[0]["phase"] == "reserved"
        snapshot[0]["intent"]["request_digest"] = "b" * 64
        assert (
            session.snapshot(intent["operation_id"], intent["actor_id"])[0]["intent"][
                "request_digest"
            ]
            == "a" * 64
        )
        with pytest.raises(ValueError, match="identity_conflict"):
            session.snapshot(intent["operation_id"], str(uuid4()))
        with pytest.raises(ValueError):
            session.snapshot("../bad", intent["actor_id"])
        assert session.snapshot(str(uuid4()), intent["actor_id"]) == []
    with pytest.raises(RuntimeError, match="session_required"):
        session.snapshot(intent["operation_id"], intent["actor_id"])

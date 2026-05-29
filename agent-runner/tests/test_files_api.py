"""Unit tests for workspace file endpoints (Phase 1: runner-side live-FS API).

Covers:
  - GET /agents/{id}/files         — version field present, deterministic
  - GET /agents/{id}/files/{path}  — single file 200 / 404
  - PUT /agents/{id}/files         — plain write returns version;
                                     If-Match match → 200 new version;
                                     If-Match mismatch → 412 + current_content
  - GET /agents/{id}/files/download — zip contains workspace files, excludes noise dirs
  - _file_version helper            — stable SHA-256[:12], changes after write

Scope: unit (tmp_path, no Docker, no testcontainers).
"""

from __future__ import annotations

import importlib
import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import routes.files as files_mod
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Bootstrap: make the runner source importable.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import RunnerSettings  # noqa: E402
from workspace import IGNORED_DIRS, _file_version  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def file_client_factory():
    """Return a factory that builds a TestClient pointing at a tmp workspace.

    Usage::

        def test_foo(tmp_path, file_client_factory):
            client = file_client_factory(tmp_path)
    """

    def _build(workspace_root: Path) -> TestClient:
        fresh_settings = RunnerSettings(
            runner_key="test-key",
            workspace_root=workspace_root,
        )

        # Reload to re-execute module-level ``settings = get_settings()``.
        importlib.reload(files_mod)
        files_mod.settings = fresh_settings

        # Wire a permissive quota mock.
        quota_mock = MagicMock()
        quota_mock.is_checkpoint_path.return_value = False
        quota_mock.check_quota_async = AsyncMock(return_value=(0.0, True))
        quota_mock.get_limits.return_value = (150, 200)
        quota_mock.handle_soft_breach = AsyncMock()
        quota_mock.invalidate.return_value = None
        files_mod.disk_quota = quota_mock

        app = FastAPI()
        app.include_router(files_mod.router)
        return TestClient(app, raise_server_exceptions=False)

    return _build


# ---------------------------------------------------------------------------
# _file_version helper
# ---------------------------------------------------------------------------


class TestFileVersion:
    def test_deterministic_same_content(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("hello world", encoding="utf-8")
        assert _file_version(f) == _file_version(f)

    def test_length_12(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_bytes(b"\x00\xff\xfe\xfd")
        assert len(_file_version(f)) == 12

    def test_changes_after_write(self, tmp_path: Path) -> None:
        f = tmp_path / "data.txt"
        f.write_text("original", encoding="utf-8")
        v_before = _file_version(f)
        f.write_text("modified", encoding="utf-8")
        assert _file_version(f) != v_before

    def test_binary_file(self, tmp_path: Path) -> None:
        f = tmp_path / "img.bin"
        f.write_bytes(bytes(range(256)))
        v = _file_version(f)
        assert len(v) == 12
        assert v.isalnum()


# ---------------------------------------------------------------------------
# GET /agents/{id}/files  (list)
# ---------------------------------------------------------------------------


class TestListWorkspaceFiles:
    def test_returns_version_field(self, tmp_path: Path, file_client_factory) -> None:
        workspace = tmp_path / "agent-1"
        workspace.mkdir()
        (workspace / "main.py").write_text("print('hi')", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-1/files")
        assert resp.status_code == 200
        files = resp.json()["files"]
        assert len(files) == 1
        assert "version" in files[0]
        assert files[0]["version"] is not None
        assert len(files[0]["version"]) == 12

    def test_noise_dirs_excluded(self, tmp_path: Path, file_client_factory) -> None:
        workspace = tmp_path / "agent-2"
        workspace.mkdir()
        (workspace / "__pycache__").mkdir()
        (workspace / "__pycache__" / "foo.pyc").write_bytes(b"cache")
        (workspace / "real.py").write_text("x=1", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-2/files")
        paths = [f["file_path"] for f in resp.json()["files"]]
        assert "real.py" in paths
        assert not any("__pycache__" in p for p in paths)

    def test_missing_workspace_returns_empty(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        client = file_client_factory(tmp_path)
        resp = client.get("/agents/nonexistent/files")
        assert resp.json() == {"files": []}


# ---------------------------------------------------------------------------
# GET /agents/{id}/files/{file_path}  (single file)
# ---------------------------------------------------------------------------


class TestGetSingleFile:
    def test_200_returns_content_and_version(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-s"
        workspace.mkdir()
        (workspace / "hello.txt").write_text("world", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-s/files/hello.txt")
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_path"] == "hello.txt"
        assert body["content"] == "world"
        assert body["is_binary"] is False
        assert body["truncated"] is False
        assert len(body["version"]) == 12

    def test_404_for_missing_file(self, tmp_path: Path, file_client_factory) -> None:
        workspace = tmp_path / "agent-s2"
        workspace.mkdir()

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-s2/files/ghost.txt")
        assert resp.status_code == 404

    def test_binary_file_has_none_content(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-s3"
        workspace.mkdir()
        (workspace / "data.bin").write_bytes(bytes(range(256)))

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-s3/files/data.bin")
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_binary"] is True
        assert body["content"] is None
        assert len(body["version"]) == 12

    def test_version_consistent_with_list(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-s4"
        workspace.mkdir()
        (workspace / "note.txt").write_text("consistent", encoding="utf-8")

        client = file_client_factory(tmp_path)
        list_files = client.get("/agents/agent-s4/files").json()["files"]
        single = client.get("/agents/agent-s4/files/note.txt").json()
        assert list_files[0]["version"] == single["version"]

    def test_404_when_workspace_missing(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        client = file_client_factory(tmp_path)
        resp = client.get("/agents/no-ws/files/any.txt")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /agents/{id}/files  (write with optimistic lock)
# ---------------------------------------------------------------------------


class TestWriteFile:
    def test_plain_write_returns_version(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-w"
        workspace.mkdir()

        client = file_client_factory(tmp_path)
        resp = client.put(
            "/agents/agent-w/files",
            json={"file_path": "test.txt", "content": "hello"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "written"
        assert "version" in body
        assert len(body["version"]) == 12

    def test_if_match_match_returns_200_new_version(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-ifm"
        workspace.mkdir()
        target = workspace / "file.txt"
        target.write_text("v1", encoding="utf-8")
        v1 = _file_version(target)

        client = file_client_factory(tmp_path)
        resp = client.put(
            "/agents/agent-ifm/files",
            json={"file_path": "file.txt", "content": "v2"},
            headers={"If-Match": v1},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "written"
        # Version must change after content update.
        assert body["version"] != v1

    def test_if_match_mismatch_returns_412(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-412"
        workspace.mkdir()
        (workspace / "conflict.txt").write_text("server version", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.put(
            "/agents/agent-412/files",
            json={"file_path": "conflict.txt", "content": "client version"},
            headers={"If-Match": "000000000000"},  # stale/wrong token
        )
        assert resp.status_code == 412
        detail = resp.json().get("detail", resp.json())
        assert detail["current_version"] is not None
        assert len(detail["current_version"]) == 12

    def test_if_match_mismatch_includes_current_content(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-412b"
        workspace.mkdir()
        (workspace / "file.txt").write_text("server content", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.put(
            "/agents/agent-412b/files",
            json={"file_path": "file.txt", "content": "nope"},
            headers={"If-Match": "000000000000"},
        )
        assert resp.status_code == 412
        detail = resp.json().get("detail", resp.json())
        assert detail["current_content"] == "server content"

    def test_if_match_on_new_file_creates_without_conflict(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        """If-Match on a non-existing file: write proceeds (no current version to compare)."""
        workspace = tmp_path / "agent-new"
        workspace.mkdir()

        client = file_client_factory(tmp_path)
        resp = client.put(
            "/agents/agent-new/files",
            json={"file_path": "brand_new.txt", "content": "fresh"},
            headers={"If-Match": "anything"},
        )
        assert resp.status_code == 200

    def test_no_if_match_writes_unconditionally(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-unc"
        workspace.mkdir()
        (workspace / "old.txt").write_text("old", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.put(
            "/agents/agent-unc/files",
            json={"file_path": "old.txt", "content": "new"},
        )
        assert resp.status_code == 200
        assert (workspace / "old.txt").read_text() == "new"

    def test_version_after_write_matches_file_on_disk(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-vd"
        workspace.mkdir()

        client = file_client_factory(tmp_path)
        client.put(
            "/agents/agent-vd/files",
            json={"file_path": "x.txt", "content": "stable"},
        )
        on_disk = _file_version(workspace / "x.txt")
        resp = client.get("/agents/agent-vd/files/x.txt")
        assert resp.json()["version"] == on_disk


# ---------------------------------------------------------------------------
# GET /agents/{id}/files/download  (zip export)
# ---------------------------------------------------------------------------


class TestDownloadWorkspaceZip:
    def test_zip_contains_workspace_files(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-dl"
        workspace.mkdir()
        (workspace / "a.txt").write_text("content a", encoding="utf-8")
        (workspace / "sub").mkdir()
        (workspace / "sub" / "b.txt").write_text("content b", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-dl/files/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
        assert "a.txt" in names
        assert "sub/b.txt" in names

    def test_zip_excludes_noise_dirs(self, tmp_path: Path, file_client_factory) -> None:
        workspace = tmp_path / "agent-dlnoise"
        workspace.mkdir()
        (workspace / "__pycache__").mkdir()
        (workspace / "__pycache__" / "foo.pyc").write_bytes(b"junk")
        (workspace / "node_modules").mkdir()
        (workspace / "node_modules" / "pkg.js").write_text("module", encoding="utf-8")
        (workspace / "real.py").write_text("real", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-dlnoise/files/download")
        assert resp.status_code == 200

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
        assert "real.py" in names
        assert not any("__pycache__" in n for n in names)
        assert not any("node_modules" in n for n in names)

    def test_zip_includes_binary_files(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-dlbin"
        workspace.mkdir()
        raw = bytes(range(256))
        (workspace / "data.bin").write_bytes(raw)

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-dlbin/files/download")
        assert resp.status_code == 200

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            assert "data.bin" in zf.namelist()
            assert zf.read("data.bin") == raw

    def test_download_404_for_missing_workspace(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        client = file_client_factory(tmp_path)
        resp = client.get("/agents/ghost-agent/files/download")
        assert resp.status_code == 404

    def test_download_route_not_captured_by_file_path_wildcard(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        """'download' must be served by the download handler, not the single-file route."""
        workspace = tmp_path / "agent-rt"
        workspace.mkdir()

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-rt/files/download")
        # Must be a zip response, not a 404 "File not found" from the single-file route.
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert resp.headers["content-type"] == "application/zip"


# ---------------------------------------------------------------------------
# Canonical IGNORED_DIRS set
# ---------------------------------------------------------------------------


class TestIgnoredDirs:
    def test_canonical_set_contains_expected_entries(self) -> None:
        expected = {
            ".git",
            "__pycache__",
            "node_modules",
            ".venv",
            "venv",
            ".pip",
            ".cache",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
        }
        assert expected == set(IGNORED_DIRS)

    def test_is_frozenset(self) -> None:
        assert isinstance(IGNORED_DIRS, frozenset)


# ---------------------------------------------------------------------------
# include_hidden=false  (default) — prune at os.walk level
# ---------------------------------------------------------------------------


class TestListIncludeHidden:
    def test_default_hides_node_modules(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-ih1"
        workspace.mkdir()
        nm = workspace / "node_modules"
        nm.mkdir()
        (nm / "x.js").write_text("module.exports={}", encoding="utf-8")
        (workspace / "app.py").write_text("pass", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-ih1/files")
        assert resp.status_code == 200
        paths = [f["file_path"] for f in resp.json()["files"]]
        assert "app.py" in paths
        assert not any("node_modules" in p for p in paths)

    def test_include_hidden_true_shows_node_modules(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-ih2"
        workspace.mkdir()
        nm = workspace / "node_modules"
        nm.mkdir()
        (nm / "x.js").write_text("module.exports={}", encoding="utf-8")
        (workspace / "app.py").write_text("pass", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-ih2/files?include_hidden=true")
        assert resp.status_code == 200
        paths = [f["file_path"] for f in resp.json()["files"]]
        assert "app.py" in paths
        assert any("node_modules" in p for p in paths)

    def test_default_prunes_all_ignored_dirs(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        """File deeply nested inside an ignored dir must be absent from default listing."""
        workspace = tmp_path / "agent-ih3"
        workspace.mkdir()
        deep = workspace / ".venv" / "lib" / "python3.12" / "site-packages"
        deep.mkdir(parents=True)
        (deep / "secret.py").write_text("# installed lib", encoding="utf-8")
        (workspace / "main.py").write_text("# user file", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-ih3/files")
        paths = [f["file_path"] for f in resp.json()["files"]]
        # Deeply nested .venv file absent
        assert not any(".venv" in p for p in paths)
        # User file present
        assert "main.py" in paths

    def test_dotfiles_not_in_ignored_visible_always(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        """.env and .gitignore are dotfiles but NOT in IGNORED_DIRS — always visible."""
        workspace = tmp_path / "agent-ih4"
        workspace.mkdir()
        (workspace / ".env").write_text("SECRET=1", encoding="utf-8")
        (workspace / ".gitignore").write_text("*.pyc", encoding="utf-8")
        (workspace / "code.py").write_text("x=1", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-ih4/files")
        paths = [f["file_path"] for f in resp.json()["files"]]
        assert ".env" in paths
        assert ".gitignore" in paths
        assert "code.py" in paths

    def test_single_file_endpoint_explicit_path_always_returns(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        """Single-file GET /{path} with explicit path inside ignored dir → 200 (no prune)."""
        workspace = tmp_path / "agent-ih5"
        workspace.mkdir()
        nm = workspace / "node_modules"
        nm.mkdir()
        (nm / "pkg.js").write_text("module={}", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-ih5/files/node_modules/pkg.js")
        # Explicit path is always served; no pruning on single-file endpoint.
        assert resp.status_code == 200
        assert resp.json()["content"] == "module={}"


# ---------------------------------------------------------------------------
# include_hidden on download ZIP
# ---------------------------------------------------------------------------


class TestDownloadIncludeHidden:
    def test_default_zip_excludes_ignored(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-dih1"
        workspace.mkdir()
        (workspace / "node_modules").mkdir()
        (workspace / "node_modules" / "a.js").write_text("x", encoding="utf-8")
        (workspace / "real.py").write_text("pass", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-dih1/files/download")
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
        assert "real.py" in names
        assert not any("node_modules" in n for n in names)

    def test_include_hidden_zip_includes_ignored(
        self, tmp_path: Path, file_client_factory
    ) -> None:
        workspace = tmp_path / "agent-dih2"
        workspace.mkdir()
        (workspace / "node_modules").mkdir()
        (workspace / "node_modules" / "a.js").write_text("x", encoding="utf-8")
        (workspace / "real.py").write_text("pass", encoding="utf-8")

        client = file_client_factory(tmp_path)
        resp = client.get("/agents/agent-dih2/files/download?include_hidden=true")
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
        assert "real.py" in names
        assert any("node_modules" in n for n in names)

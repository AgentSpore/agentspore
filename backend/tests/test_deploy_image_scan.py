"""Mandatory image scan before any registry write."""

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from test_deploy_buildkit import ready as ready
from test_deploy_registry_push import image_tar as image_tar

from app.core.config import Settings
from app.schemas.deployments import DeploymentLease
from app.services.deployment.builder import BuildRequest, BuildResult, FakeBuilder
from app.services.deployment.buildkit import BuildKitBuilder, builder_from_settings
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.image_plan import plan_image
from app.services.deployment.image_scan import ImageScannedBuilder, TrivyImageScanner
from app.services.deployment.image_scan_artifact import VerifiedScanInput
from app.services.deployment.launcher_runtime import LauncherRuntime
from app.services.deployment.recycle import DaemonRecycler
from app.services.deployment.registry_push import PushingBuilder
from app.services.deployment.worker import DeploymentWorker

CONFIG = {
    "os": "linux",
    "architecture": "amd64",
    "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "a" * 64]},
}


@pytest.fixture
def scan_result(request):
    artifact = request.getfixturevalue("image_tar")(config=json.dumps(CONFIG).encode())
    return BuildResult(
        image_ref="local@" + plan_image(artifact).manifest.digest,
        image_bytes=artifact.stat().st_size,
        artifact=artifact,
    )


@pytest.fixture
def scanner_tool():
    async def run(argv, *, cwd, timeout, env):
        assert Path(env["TMPDIR"]).parent == cwd
        assert Path(env["TMPDIR"]).is_dir()
        cache = Path(argv[argv.index("--cache-dir") + 1])
        for flag, folder, filename, version in (
            ("--download-db-only", "db", "trivy.db", 2),
            ("--download-java-db-only", "java-db", "trivy-java.db", 1),
        ):
            if flag in argv:
                dest = cache / folder
                dest.mkdir(parents=True)
                (dest / filename).write_bytes(b"synthetic-database")
                now = datetime.now(timezone.utc)
                (dest / "metadata.json").write_text(
                    json.dumps(
                        {
                            "Version": version,
                            "UpdatedAt": now.isoformat(),
                            "NextUpdate": (now + timedelta(hours=6)).isoformat(),
                        }
                    )
                )
                return 0, b""
        layout = Path(argv[argv.index("--input") + 1].split("@")[0])
        manifest = json.loads((layout / "index.json").read_bytes())["manifests"][0]["digest"]
        config_digest = json.loads((layout / "blobs/sha256" / manifest[7:]).read_bytes())["config"][
            "digest"
        ]
        config = json.loads((layout / "blobs/sha256" / config_digest[7:]).read_bytes())
        return 0, json.dumps(
            {
                "SchemaVersion": 2,
                "ArtifactType": "container_image",
                "Metadata": {
                    "OS": {"Family": "alpine", "Name": "3.22"},
                    "ImageID": config_digest,
                    "ImageConfig": config,
                    "DiffIDs": config.get("rootfs", {}).get("diff_ids"),
                },
                "Results": [
                    {"Class": "os-pkgs", "Type": "alpine", "Packages": [{"Name": "busybox"}]}
                ],
            }
        ).encode()

    return AsyncMock(side_effect=run)


async def test_clean_archive_scanned_before_push(scan_result, scanner_tool, tmp_path):
    scanner = TrivyImageScanner(tmp_path, run=scanner_tool)
    pusher = AsyncMock()
    pusher.push.return_value = "registry@" + scan_result.image_ref.split("@")[1]
    builder = PushingBuilder(ImageScannedBuilder(FakeBuilder(result=scan_result), scanner), pusher)
    await builder.build(BuildRequest(context_dir=tmp_path, tag="synthetic:sha"))
    assert scanner_tool.await_count == 3
    assert pusher.push.await_count == 1
    assert not scan_result.artifact.exists()
    assert not list(tmp_path.glob("a4-scan-*"))


async def test_critical_never_pushes(scan_result, scanner_tool, tmp_path):
    original = scanner_tool.side_effect

    async def critical(argv, **kwargs):
        rc, body = await original(argv, **kwargs)
        if "--input" in argv:
            report = json.loads(body)
            report["Results"][0]["Vulnerabilities"] = [{"Severity": "CRITICAL"}]
            return 42, json.dumps(report).encode()
        return rc, body

    scanner_tool.side_effect = critical
    pusher = AsyncMock()
    builder = PushingBuilder(
        ImageScannedBuilder(
            FakeBuilder(result=scan_result), TrivyImageScanner(tmp_path, run=scanner_tool)
        ),
        pusher,
    )
    with pytest.raises(DeployRejectedError) as caught:
        await builder.build(BuildRequest(context_dir=tmp_path, tag="synthetic:sha"))
    assert caught.value.reason_code == "image_vulnerable"
    pusher.push.assert_not_called()
    assert not scan_result.artifact.exists()


@pytest.mark.parametrize(
    "mode",
    [
        "bad_json",
        "empty",
        "bad_schema",
        "no_os",
        "no_packages",
        "bad_vulnerabilities",
        "exit_mismatch",
        "tool_error",
    ],
)
async def test_invalid_report_never_clean(scan_result, scanner_tool, tmp_path, mode):
    original = scanner_tool.side_effect

    async def invalid(argv, **kwargs):
        rc, body = await original(argv, **kwargs)
        if "--input" not in argv:
            return rc, body
        if mode == "bad_json":
            return 0, b"not json with synthetic untrusted detail"
        if mode == "empty":
            return 0, b"{}"
        data = json.loads(body)
        if mode == "bad_schema":
            data["SchemaVersion"] = 99
        if mode == "no_os":
            data["Metadata"] = {}
        if mode == "no_packages":
            data["Results"][0].pop("Packages")
        if mode == "bad_vulnerabilities":
            data["Results"][0]["Vulnerabilities"] = {}
        return (42 if mode == "exit_mismatch" else 1 if mode == "tool_error" else 0), json.dumps(
            data
        ).encode()

    scanner_tool.side_effect = invalid
    with pytest.raises(DeployRejectedError) as caught:
        await TrivyImageScanner(tmp_path, run=scanner_tool).scan(scan_result)
    assert caught.value.reason_code == "tool_failed"
    assert "synthetic untrusted detail" not in str(caught.value)
    assert not list(tmp_path.glob("a4-scan-*"))


@pytest.mark.parametrize(
    "mode", ["stale", "future", "no_timezone", "schema", "missing", "oversized"]
)
async def test_database_policy_fails_closed(scan_result, scanner_tool, tmp_path, mode):
    original = scanner_tool.side_effect

    async def invalid(argv, **kwargs):
        rc, body = await original(argv, **kwargs)
        if "--download-java-db-only" in argv:
            root = Path(argv[argv.index("--cache-dir") + 1]) / "db"
            file = root / "metadata.json"
            data = json.loads(file.read_text())
            if mode == "stale":
                data["UpdatedAt"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
            if mode == "future":
                data["UpdatedAt"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            if mode == "no_timezone":
                data["UpdatedAt"] = "2026-11-02T00:00:00"
            if mode == "schema":
                data["Version"] = 999
            file.write_text(json.dumps(data))
            if mode == "missing":
                (root / "trivy.db").unlink()
            if mode == "oversized":
                with (root / "trivy.db").open("wb") as output:
                    output.truncate(TrivyImageScanner.MAX_DB_BYTES + 1)
        return rc, body

    scanner_tool.side_effect = invalid
    with pytest.raises(DeployRejectedError) as caught:
        await TrivyImageScanner(tmp_path, run=scanner_tool).scan(scan_result)
    assert caught.value.reason_code == "tool_failed"
    assert scanner_tool.await_count == 2


@pytest.mark.parametrize("change", ["archive", "layout", "replacement"])
async def test_scan_binds_exact_pushed_bytes(scan_result, scanner_tool, tmp_path, change):
    original = scanner_tool.side_effect

    async def mutate(argv, **kwargs):
        rc, body = await original(argv, **kwargs)
        if "--input" in argv:
            if change == "archive":
                with scan_result.artifact.open("ab") as target:
                    target.write(b"changed")
            elif change == "replacement":
                data = scan_result.artifact.read_bytes()
                replacement = tmp_path / "replacement.tar"
                replacement.write_bytes(data)
                assert replacement.stat().st_ino != scan_result.artifact.stat().st_ino
                replacement.replace(scan_result.artifact)
            else:
                layout = Path(argv[argv.index("--input") + 1].split("@")[0])
                index = layout / "index.json"
                index.chmod(0o600)
                index.write_text("{}")
        return rc, body

    scanner_tool.side_effect = mutate
    pusher = AsyncMock()
    pusher.push.return_value = scan_result.image_ref
    builder = PushingBuilder(
        ImageScannedBuilder(
            FakeBuilder(result=scan_result), TrivyImageScanner(tmp_path, run=scanner_tool)
        ),
        pusher,
    )
    with pytest.raises(DeployRejectedError):
        await builder.build(BuildRequest(context_dir=tmp_path, tag="synthetic:sha"))
    pusher.push.assert_not_called()
    assert not scan_result.artifact.exists()


async def test_fixed_policy_uses_private_workspace(scan_result, scanner_tool, tmp_path):
    (tmp_path / "trivy.yaml").write_text("severity: []")
    await TrivyImageScanner(tmp_path, run=scanner_tool).scan(scan_result)
    args = scanner_tool.call_args.args[0]
    work = scanner_tool.call_args.kwargs["cwd"]
    assert work.parent == tmp_path and work != tmp_path
    assert args[args.index("--severity") + 1] == "CRITICAL"
    assert args[args.index("--scanners") + 1] == "vuln"
    assert "--offline-scan" in args and "--skip-db-update" in args
    assert "--skip-java-db-update" in args and "--disable-telemetry" in args
    assert str(work) in args[args.index("--ignorefile") + 1]
    staging = {call.kwargs["env"]["TMPDIR"] for call in scanner_tool.call_args_list}
    assert staging == {str(work / "staging")}
    assert not (work / "staging").exists()


async def test_cancelled_scanner_cleans_artifact(scan_result, scanner_tool, tmp_path):
    entered = asyncio.Event()
    original = scanner_tool.side_effect

    async def wait(argv, **kwargs):
        if "--input" in argv:
            entered.set()
            await asyncio.Future()
        return await original(argv, **kwargs)

    scanner_tool.side_effect = wait
    builder = ImageScannedBuilder(
        FakeBuilder(result=scan_result), TrivyImageScanner(tmp_path, run=scanner_tool)
    )
    task = asyncio.create_task(
        builder.build(BuildRequest(context_dir=tmp_path, tag="synthetic:sha"))
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not scan_result.artifact.exists()
    assert not list(tmp_path.glob("a4-scan-*"))


async def test_thread_finishes_before_cancellation_cleanup():
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def operation():
        entered.set()
        release.wait(5)
        finished.set()

    task = asyncio.create_task(TrivyImageScanner._joined(operation))
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_foreign_artifact_not_deleted(scan_result, scanner_tool, tmp_path):
    owned = tmp_path / "owned"
    owned.mkdir()
    builder = ImageScannedBuilder(
        FakeBuilder(result=scan_result), TrivyImageScanner(owned, run=scanner_tool)
    )
    with pytest.raises(DeployRejectedError):
        await builder.build(BuildRequest(context_dir=tmp_path, tag="synthetic:sha"))
    assert scan_result.artifact.exists()
    scanner_tool.assert_not_called()


@pytest.mark.parametrize("critical", [False, True])
async def test_real_builder_wiring_cannot_skip_image_gate(
    request, scan_result, scanner_tool, tmp_path, monkeypatch, critical
):
    config = request.getfixturevalue("ready")
    config["a4_builder_out_dir"] = tmp_path
    events = []

    async def build(self, build_request):
        events.append("build")
        return scan_result

    async def restart(self, client):
        events.append("recycle")

    async def healthy(self, client):
        return None

    original = scanner_tool.side_effect

    async def scan(argv, **kwargs):
        rc, body = await original(argv, **kwargs)
        if "--input" in argv:
            events.append("scan")
            if critical:
                data = json.loads(body)
                data["Results"][0]["Vulnerabilities"] = [{"Severity": "CRITICAL"}]
                return 42, json.dumps(data).encode()
        return rc, body

    async def push(tar, repo):
        events.append("push")
        return "registry@" + scan_result.image_ref.split("@")[1]

    scanner_tool.side_effect = scan
    monkeypatch.setattr(BuildKitBuilder, "build", build)
    monkeypatch.setattr(DaemonRecycler, "_restart", restart)
    monkeypatch.setattr(DaemonRecycler, "_wait_ready", healthy)
    # Patch only subprocess I/O; production selector must include and execute the real gate.
    monkeypatch.setattr(
        "app.services.deployment.buildkit.TrivyImageScanner",
        lambda out: TrivyImageScanner(out, run=scanner_tool),
    )
    builder = builder_from_settings(Settings(**config))
    assert isinstance(builder, PushingBuilder)
    builder.pusher.push = AsyncMock(side_effect=push)
    if critical:
        with pytest.raises(DeployRejectedError) as caught:
            await builder.build(BuildRequest(context_dir=tmp_path, tag="synthetic:sha"))
        assert caught.value.reason_code == "image_vulnerable"
        assert events == ["recycle", "build", "recycle", "scan"]
        builder.pusher.push.assert_not_called()
    else:
        await builder.build(BuildRequest(context_dir=tmp_path, tag="synthetic:sha"))
        assert events == ["recycle", "build", "recycle", "scan", "push"]


async def test_worker_refusal_never_launches(scan_result, scanner_tool, tmp_path):
    scanner_tool.side_effect = None
    scanner_tool.return_value = (1, b"untrusted raw output")
    builder = ImageScannedBuilder(
        FakeBuilder(result=scan_result), TrivyImageScanner(tmp_path, run=scanner_tool)
    )

    async def fetch(repo, sha, destination):
        destination.mkdir()
        (destination / "Dockerfile").write_text("FROM scratch\n")

    worker = DeploymentWorker(MagicMock(), builder, [], fetch=fetch)
    runtime = MagicMock(spec=LauncherRuntime)
    runtime.launch = AsyncMock()
    worker.runtime = runtime
    reason = await worker._pipeline(
        DeploymentLease(deployment_id=uuid4(), worker_id=uuid4(), generation=1),
        "https://example.test/fixture",
        "a" * 40,
    )
    assert reason == "tool_failed"
    runtime.launch.assert_not_called()


async def test_concurrent_jobs_have_disjoint_private_databases(request, scanner_tool, tmp_path):
    make = request.getfixturevalue("image_tar")
    paths = [
        make(config=json.dumps(CONFIG).encode(), name=f"image-{index}.tar") for index in range(2)
    ]
    results = [
        BuildResult(
            image_ref="local@" + plan_image(path).manifest.digest,
            image_bytes=path.stat().st_size,
            artifact=path,
        )
        for path in paths
    ]
    barrier = asyncio.Barrier(2)
    caches = []
    original = scanner_tool.side_effect

    async def scan(argv, **kwargs):
        rc, body = await original(argv, **kwargs)
        if "--input" in argv:
            caches.append(Path(argv[argv.index("--cache-dir") + 1]))
            await barrier.wait()
            assert all(cache.is_dir() for cache in caches)
        return rc, body

    scanner_tool.side_effect = scan
    scanner = TrivyImageScanner(tmp_path, run=scanner_tool)
    async with asyncio.TaskGroup() as group:
        for result in results:
            group.create_task(scanner.scan(result))
    assert len(set(caches)) == 2
    assert not list(tmp_path.glob("a4-scan-*"))


@pytest.mark.parametrize("mode", ["clean", "critical", "wrong_id", "wrong_rootfs", "wrong_arch"])
async def test_osless_report_bound_to_verified_image(scan_result, scanner_tool, tmp_path, mode):
    original = scanner_tool.side_effect

    async def osless(argv, **kwargs):
        rc, body = await original(argv, **kwargs)
        if "--input" not in argv:
            return rc, body
        data = json.loads(body)
        data["Metadata"].pop("OS")
        data.pop("Results")
        if mode == "critical":
            data["Results"] = [
                {
                    "Class": "lang-pkgs",
                    "Type": "gobinary",
                    "Packages": [{"Name": "module"}],
                    "Vulnerabilities": [{"Severity": "CRITICAL"}],
                }
            ]
            rc = 42
        if mode == "wrong_id":
            data["Metadata"]["ImageID"] = "sha256:" + "f" * 64
        if mode == "wrong_rootfs":
            data["Metadata"]["DiffIDs"] = []
        if mode == "wrong_arch":
            data["Metadata"]["ImageConfig"]["architecture"] = "arm64"
        return rc, json.dumps(data).encode()

    scanner_tool.side_effect = osless
    scanner = TrivyImageScanner(tmp_path, run=scanner_tool)
    if mode == "clean":
        await scanner.scan(scan_result)
    else:
        with pytest.raises(DeployRejectedError) as caught:
            await scanner.scan(scan_result)
        assert caught.value.reason_code == (
            "image_vulnerable" if mode == "critical" else "tool_failed"
        )
    assert not list(tmp_path.glob("a4-scan-*"))


@pytest.mark.parametrize("directory,hours", [("db", 24), ("java-db", 72)])
@pytest.mark.parametrize(
    "mode", ["boundary", "expired", "next_expired", "future", "invalid", "no_next_tz", "order"]
)
def test_database_expiry_boundaries(tmp_path, monkeypatch, directory, hours, mode):
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    clock = MagicMock()
    clock.now.return_value = now
    clock.fromisoformat.side_effect = datetime.fromisoformat
    monkeypatch.setattr("app.services.deployment.image_scan.datetime", clock)
    for folder, filename, version, max_age in TrivyImageScanner.DB_FILES:
        root = tmp_path / folder
        root.mkdir()
        (root / filename).write_bytes(b"synthetic")
        updated, next_update = now - timedelta(hours=1), now + timedelta(hours=1)
        if folder == directory:
            updated = now - timedelta(hours=hours)
            if mode == "expired":
                updated -= timedelta(microseconds=1)
            if mode == "next_expired":
                next_update = now
            if mode == "future":
                updated = now + timedelta(microseconds=1)
            if mode == "order":
                next_update = updated
        data = {
            "Version": version,
            "UpdatedAt": updated.isoformat(),
            "NextUpdate": next_update.isoformat(),
            "DownloadedAt": now.isoformat(),
        }
        if folder == directory and mode == "invalid":
            data["UpdatedAt"] = "invalid"
        if folder == directory and mode == "no_next_tz":
            data["NextUpdate"] = next_update.replace(tzinfo=None).isoformat()
        (root / "metadata.json").write_text(json.dumps(data))
    if mode == "boundary":
        TrivyImageScanner._validate_databases(tmp_path)
    else:
        with pytest.raises(ValueError):
            TrivyImageScanner._validate_databases(tmp_path)


@pytest.mark.parametrize("config", [b"[]", b"null", b"42"])
def test_report_binding_rejects_non_object_config(tmp_path, request, config):
    artifact = request.getfixturevalue("image_tar")(config=config)
    result = BuildResult(
        image_ref="local@" + plan_image(artifact).manifest.digest,
        image_bytes=artifact.stat().st_size,
        artifact=artifact,
    )
    workspace = tmp_path / "private"
    source = VerifiedScanInput.prepare(result, tmp_path, workspace)
    with pytest.raises(ValueError, match="image config invalid"):
        source.verify_report_identity({})

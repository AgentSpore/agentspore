"""The worker deletes the built tarball until a push step consumes it."""

from unittest.mock import MagicMock

from app.services.deployment import worker as worker_module
from app.services.deployment.builder import BuildResult, FakeBuilder
from app.services.deployment.worker import DeploymentWorker


async def test_successful_build_leaves_no_tarball(tmp_path, monkeypatch):
    tar = tmp_path / "img.tar"
    tar.write_bytes(b"x")

    async def no_scan(root, scanners):
        return None

    async def fetch(repo_url, commit_sha, dest):
        return None

    monkeypatch.setattr(worker_module, "load_manifest", lambda root: None)
    monkeypatch.setattr(worker_module, "run_prescan", no_scan)
    builder = FakeBuilder(result=BuildResult(image_ref="t", image_bytes=1, artifact=tar))
    worker = DeploymentWorker(MagicMock(), builder, [], fetch=fetch)
    lease = MagicMock(sandbox_namespace="ns", deployment_id="d")

    reason = await worker._pipeline(lease, "url", "a" * 40)

    assert reason == "runtime_unavailable"
    assert not tar.exists()

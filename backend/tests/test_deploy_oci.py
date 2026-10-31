"""A4 P3b-2a: trusted-side OCI digest binding and the builder factory."""

import io
import json
import tarfile
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.deployment.builder import BuildRequest, BuildResult, FakeBuilder
from app.services.deployment.buildkit import BuildKitBuilder, builder_from_settings
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.oci import DigestBoundBuilder, verify_oci_digest

DIGEST = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32


def index_for(*digests: str) -> bytes:
    return json.dumps({"manifests": [{"digest": d} for d in digests]}).encode()


def write_tar(path: Path, members: dict[str, bytes], extra=()) -> Path:
    with tarfile.open(path, "w") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for info in extra:
            tf.addfile(info)
    return path


def link(kind: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo("blobs/evil")
    info.type = kind
    info.linkname = "/etc/passwd"
    return info


def test_matching_digest_passes(tmp_path):
    tar = write_tar(tmp_path / "a.tar", {"index.json": index_for(DIGEST), "blobs/x": b"1"})
    verify_oci_digest(tar, DIGEST)
    assert tar.exists()


@pytest.mark.parametrize(
    ("members", "extra"),
    [
        ({"index.json": index_for(OTHER)}, ()),
        ({"blobs/x": b"1"}, ()),
        ({"index.json": b"x" * (64 * 1024 + 1)}, ()),
        ({"index.json": b"not json"}, ()),
        ({"index.json": index_for(DIGEST, OTHER)}, ()),
        ({"index.json": index_for(DIGEST)}, (link(tarfile.SYMTYPE),)),
        ({"index.json": index_for(DIGEST)}, (link(tarfile.LNKTYPE),)),
        ({"index.json": index_for(DIGEST), "../escape": b"1"}, ()),
    ],
    ids=[
        "mismatch", "no-index", "oversized", "not-json", "two-manifests",
        "symlink", "hardlink", "dotdot",
    ],
)  # fmt: skip
def test_bad_tar_is_refused(tmp_path, members, extra):
    tar = write_tar(tmp_path / "a.tar", members, extra)
    with pytest.raises(DeployRejectedError) as err:
        verify_oci_digest(tar, DIGEST)
    assert err.value.reason_code == "build_failed"


def test_unreadable_tar_is_refused(tmp_path):
    tar = tmp_path / "a.tar"
    tar.write_bytes(b"x" * 2048)
    with pytest.raises(DeployRejectedError):
        verify_oci_digest(tar, DIGEST)


async def test_bound_builder_removes_tar_on_mismatch(tmp_path):
    tar = write_tar(tmp_path / "a.tar", {"index.json": index_for(OTHER)})
    inner = FakeBuilder(result=BuildResult(image_ref=f"{tar}@{DIGEST}", image_bytes=1))
    with pytest.raises(DeployRejectedError):
        await DigestBoundBuilder(inner).build(BuildRequest(context_dir=tmp_path, tag="t"))
    assert not tar.exists()


async def test_bound_builder_keeps_tar_on_match(tmp_path):
    tar = write_tar(tmp_path / "a.tar", {"index.json": index_for(DIGEST)})
    result = BuildResult(image_ref=f"{tar}@{DIGEST}", image_bytes=1)
    got = await DigestBoundBuilder(FakeBuilder(result=result)).build(
        BuildRequest(context_dir=tmp_path, tag="t")
    )
    assert got == result
    assert tar.exists()


def test_default_settings_give_fake_builder():
    assert isinstance(builder_from_settings(Settings(_env_file=None)), FakeBuilder)


def test_buildkit_settings_give_bound_buildkit(tmp_path):
    cfg = Settings(
        _env_file=None,
        a4_builder="buildkit",
        a4_buildkit_addr="tcp://buildkitd:1234",
        a4_buildkit_tls_dir=tmp_path / "tls",
        a4_builder_out_dir=tmp_path / "out",
        a4_builder_proxy_url="http://10.213.77.11:3128",
    )
    built = builder_from_settings(cfg)
    assert isinstance(built, DigestBoundBuilder)
    inner = built.inner
    assert isinstance(inner, BuildKitBuilder)
    assert (inner.addr, inner.tls_dir, inner.out_dir) == (
        "tcp://buildkitd:1234",
        tmp_path / "tls",
        tmp_path / "out",
    )
    assert inner.proxy_url == "http://10.213.77.11:3128"


def test_unknown_builder_is_rejected():
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{"a4_builder": "docker"})

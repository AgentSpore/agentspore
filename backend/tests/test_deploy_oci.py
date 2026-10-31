"""A4 P3b-2a: trusted-side OCI digest binding and the builder factory."""

import gzip
import io
import json
import tarfile
import tracemalloc
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.deployment import oci
from app.services.deployment.builder import BuildRequest, BuildResult, FakeBuilder
from app.services.deployment.buildkit import BuildKitBuilder, builder_from_settings
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.oci import DigestBoundBuilder, verify_oci_digest
from app.services.deployment.recycle import RecyclingBuilder

DIGEST = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32
BLOB = "blobs/sha256/" + "e" * 64


def index_for(*digests: str) -> bytes:
    return json.dumps({"manifests": [{"digest": d} for d in digests]}).encode()


def write_tar(path: Path, members, extra=(), fmt=tarfile.USTAR_FORMAT) -> Path:
    items = members.items() if isinstance(members, dict) else members
    with tarfile.open(path, "w", format=fmt) as tf:
        for name, data in items:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for info in extra:
            tf.addfile(info)
    return path


def link(kind: bytes, name: str = "blobs/evil") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = "/etc/passwd"
    return info


def test_matching_digest_passes(tmp_path):
    tar = write_tar(tmp_path / "a.tar", {"index.json": index_for(DIGEST), BLOB: b"1"})
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
        ({"index.json": index_for(DIGEST), "/abs": b"1"}, ()),
        ({"index.json": index_for(DIGEST) + b" " * (64 * 1024)}, ()),
        ([("index.json", index_for(OTHER)), ("index.json", index_for(DIGEST))], ()),
        ([("index.json", index_for(DIGEST)), ("./index.json", index_for(OTHER))], ()),
        ([("index.json", index_for(DIGEST))] + [(f"b{n}", b"") for n in range(10_001)], ()),
    ],
    ids=[
        "mismatch", "no-index", "oversized", "not-json", "two-manifests",
        "symlink", "hardlink", "dotdot", "absolute", "padded-json", "duplicate", "dot-alias",
        "too-many-members",
    ],
)  # fmt: skip
def test_bad_tar_is_refused(tmp_path, members, extra):
    tar = write_tar(tmp_path / "a.tar", members, extra)
    with pytest.raises(DeployRejectedError) as err:
        verify_oci_digest(tar, DIGEST)
    assert err.value.reason_code == "build_failed"


def test_huge_pax_header_is_refused_without_buffering(tmp_path):
    tar = tmp_path / "pax.tar"
    with tarfile.open(tar, "w", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo("index.json")
        body = index_for(DIGEST)
        info.size = len(body)
        info.pax_headers = {"comment": "A" * 200_000_000}
        tf.addfile(info, io.BytesIO(body))
    tracemalloc.start()
    try:
        with pytest.raises(DeployRejectedError):
            verify_oci_digest(tar, DIGEST)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 16 * 1024 * 1024


def test_gzip_tar_is_refused(tmp_path):
    plain = write_tar(tmp_path / "plain.tar", {"index.json": index_for(DIGEST)})
    packed = tmp_path / "a.tar"
    packed.write_bytes(gzip.compress(plain.read_bytes()))
    with pytest.raises(DeployRejectedError):
        verify_oci_digest(packed, DIGEST)


def test_gnu_longname_is_refused(tmp_path):
    tar = write_tar(
        tmp_path / "a.tar",
        {"index.json": index_for(DIGEST), "blobs/" + "n" * 150: b"1"},
        fmt=tarfile.GNU_FORMAT,
    )
    with pytest.raises(DeployRejectedError):
        verify_oci_digest(tar, DIGEST)


def test_directory_entries_are_accepted(tmp_path):
    tar = tmp_path / "a.tar"
    with tarfile.open(tar, "w", format=tarfile.USTAR_FORMAT) as tf:
        for name in ("blobs/", "blobs/sha256/"):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            tf.addfile(info)
        body = index_for(DIGEST)
        info = tarfile.TarInfo("index.json")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))
    verify_oci_digest(tar, DIGEST)


def test_missing_index_names_the_cause(tmp_path):
    tar = write_tar(tmp_path / "a.tar", {BLOB: b"1"})
    with pytest.raises(DeployRejectedError, match="no index.json"):
        verify_oci_digest(tar, DIGEST)


def test_unreadable_tar_is_refused(tmp_path):
    tar = tmp_path / "a.tar"
    tar.write_bytes(b"x" * 2048)
    with pytest.raises(DeployRejectedError):
        verify_oci_digest(tar, DIGEST)


async def test_bound_builder_removes_tar_on_mismatch(tmp_path):
    tar = write_tar(tmp_path / "a.tar", {"index.json": index_for(OTHER)})
    inner = FakeBuilder(
        result=BuildResult(image_ref=f"{tar}@{DIGEST}", image_bytes=1, artifact=tar)
    )
    with pytest.raises(DeployRejectedError):
        await DigestBoundBuilder(inner).build(BuildRequest(context_dir=tmp_path, tag="t"))
    assert not tar.exists()


async def test_bound_builder_keeps_tar_on_match(tmp_path):
    tar = write_tar(tmp_path / "a.tar", {"index.json": index_for(DIGEST)})
    result = BuildResult(image_ref=f"{tar}@{DIGEST}", image_bytes=1, artifact=tar)
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
    assert isinstance(built, RecyclingBuilder)
    assert isinstance(built.inner, DigestBoundBuilder)
    inner = built.inner.inner
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


def entry(name: str, data: bytes = b"", fmt=tarfile.USTAR_FORMAT, pax=None) -> bytes:
    """One archive entry as raw bytes, without the end-of-archive blocks."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as tf:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.pax_headers = pax or {}
        tf.addfile(info, io.BytesIO(data))
    data = buf.getvalue()
    while data.endswith(b"\0" * 512):
        data = data[:-512]
    return data


def with_size(header: bytes, size: int) -> bytes:
    """Rewrite a ustar header's size field and fix its checksum."""
    head = bytearray(header)
    head[124:136] = b"%011o\0" % size
    head[148:156] = b" " * 8
    head[148:156] = b"%06o\0 " % sum(head)
    return bytes(head)


def raw_tar(path: Path, *parts: bytes) -> Path:
    path.write_bytes(b"".join(parts) + b"\0" * 1024)
    return path


@pytest.mark.parametrize("tail", [b"blobs/evil", b"x" * 100], ids=["symlink", "longname"])
def test_pax_size_override_cannot_desync_the_walker(tmp_path, tail):
    paxed = entry("blobs/a", fmt=tarfile.PAX_FORMAT, pax={"size": "0"})
    pax_part, ustar_head = paxed[:1024], with_size(paxed[1024:1536], 0o77777777777)
    hidden = entry(tail.decode(), fmt=tarfile.GNU_FORMAT)
    tar = raw_tar(
        tmp_path / "a.tar", pax_part, ustar_head, hidden, entry("index.json", index_for(DIGEST))
    )
    with pytest.raises(DeployRejectedError, match="unsupported key"):
        verify_oci_digest(tar, DIGEST)


@pytest.mark.parametrize("key", ["comment", "uid", "GNU.sparse.size"])
def test_other_pax_keys_are_refused(tmp_path, key):
    tar = raw_tar(
        tmp_path / "a.tar",
        entry("index.json", index_for(DIGEST), fmt=tarfile.PAX_FORMAT, pax={key: "1"}),
    )
    with pytest.raises(DeployRejectedError, match="unsupported key"):
        verify_oci_digest(tar, DIGEST)


def test_malformed_pax_record_is_refused(tmp_path):
    body = b"99 path=x\n"
    head = tarfile.TarInfo("pax")
    head.type, head.size = tarfile.XHDTYPE, len(body)
    raw = head.tobuf(tarfile.USTAR_FORMAT)
    tar = raw_tar(
        tmp_path / "a.tar", raw, body.ljust(512, b"\0"), entry("index.json", index_for(DIGEST))
    )
    with pytest.raises(DeployRejectedError, match="malformed extended header"):
        verify_oci_digest(tar, DIGEST)


def test_pax_path_naming_a_layout_blob_is_accepted(tmp_path):
    tar = raw_tar(
        tmp_path / "a.tar",
        entry("x", b"1", fmt=tarfile.PAX_FORMAT, pax={"path": BLOB}),
        entry("index.json", index_for(DIGEST)),
    )
    verify_oci_digest(tar, DIGEST)


@pytest.mark.parametrize(
    "name",
    [
        "foo",
        "indexXjson",
        "blobs/sha256/" + "e" * 63,
        "blobs/sha256/../x",
        "blobs/x",
        "blobs/sha256/" + "E" * 64,
        "./index.json",
        "/index.json",
    ],
)
def test_names_outside_the_oci_layout_are_refused(tmp_path, name):
    tar = write_tar(tmp_path / "a.tar", {"index.json": index_for(DIGEST), name: b"1"})
    with pytest.raises(DeployRejectedError):
        verify_oci_digest(tar, DIGEST)


def test_nul_in_pax_path_cannot_alias_index_json(tmp_path):
    tar = raw_tar(
        tmp_path / "a.tar",
        entry("index.json", index_for(DIGEST)),
        entry("x", index_for(OTHER), fmt=tarfile.PAX_FORMAT, pax={"path": "index.json\0junk"}),
    )
    with pytest.raises(DeployRejectedError):
        verify_oci_digest(tar, DIGEST)


def test_bad_checksum_mid_stream_is_refused(tmp_path):
    bad = bytearray(entry("junk"))
    bad[148:156] = b"0000000\0"
    tar = raw_tar(
        tmp_path / "a.tar",
        entry("index.json", index_for(DIGEST)),
        bytes(bad),
        entry("index.json", index_for(OTHER)),
    )
    with pytest.raises(DeployRejectedError):
        verify_oci_digest(tar, DIGEST)


def test_walker_and_tarfile_must_end_at_the_same_offset(tmp_path, monkeypatch):
    tar = write_tar(tmp_path / "a.tar", {"index.json": index_for(DIGEST)})
    monkeypatch.setattr(oci, "_screen_headers", lambda path: 0)
    with pytest.raises(DeployRejectedError, match="parses differently"):
        verify_oci_digest(tar, DIGEST)


@pytest.mark.parametrize("body", [b"10 path=xx", b"10 pathxxxx\n"], ids=["no-newline", "no-equals"])
def test_pax_record_needs_newline_and_equals(tmp_path, body):
    head = tarfile.TarInfo("pax")
    head.type, head.size = tarfile.XHDTYPE, len(body)
    tar = raw_tar(
        tmp_path / "a.tar",
        head.tobuf(tarfile.USTAR_FORMAT),
        body.ljust(512, b"\0"),
        entry("index.json", index_for(DIGEST)),
    )
    with pytest.raises(DeployRejectedError, match="extended header"):
        verify_oci_digest(tar, DIGEST)


def test_checksum_rule_matches_tarfile():
    block = entry("index.json", b"{}")[:512]
    assert oci._checksum_ok(block)
    assert not oci._checksum_ok(block[:148] + b"0000000\0" + block[156:])
    signed = bytearray(entry("\xe9", b"")[:512])
    signed[148:156] = b"%06o\0 " % (
        sum(signed[:148])
        + 256
        + sum(signed[156:])
        - 256 * sum(b >= 128 for b in signed[:148] + signed[156:])
    )
    assert oci._checksum_ok(bytes(signed))


def test_tarfile_loop_refuses_links_even_if_the_walker_is_bypassed(tmp_path, monkeypatch):
    tar = write_tar(
        tmp_path / "a.tar", {"index.json": index_for(DIGEST)}, (link(tarfile.SYMTYPE, BLOB),)
    )
    monkeypatch.setattr(oci, "_screen_headers", lambda path: 1536)
    with pytest.raises(DeployRejectedError, match="unexpected member"):
        verify_oci_digest(tar, DIGEST)


def test_walker_alone_refuses_a_bad_checksum(tmp_path):
    bad = bytearray(entry("junk"))
    bad[148:156] = b"0000000\0"
    tar = raw_tar(tmp_path / "a.tar", entry("index.json", index_for(DIGEST)), bytes(bad))
    with pytest.raises(DeployRejectedError):
        oci._screen_headers(tar)


def test_walker_alone_refuses_a_pax_path_outside_the_layout(tmp_path):
    tar = raw_tar(
        tmp_path / "a.tar",
        entry("x", b"1", fmt=tarfile.PAX_FORMAT, pax={"path": "index.json\0junk"}),
    )
    with pytest.raises(DeployRejectedError, match="outside the OCI layout"):
        oci._screen_headers(tar)

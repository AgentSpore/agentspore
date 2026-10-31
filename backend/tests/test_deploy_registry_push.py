"""A4 P3b-2c-b: pushing the verified image tarball to the private registry."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.services.deployment.builder import BuildRequest, BuildResult, FakeBuilder
from app.services.deployment.buildkit import builder_from_settings
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.image_plan import MAX_JSON_BYTES, MAX_LAYERS
from app.services.deployment.registry_push import (
    MAX_REPLY_BYTES,
    PushingBuilder,
    RegistryPusher,
)

BASE = "http://a4-registry:5000"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def descriptor(data: bytes, media_type: str, **extra) -> dict:
    return {"mediaType": media_type, "digest": sha(data), "size": len(data), **extra}


@pytest.fixture
def image_tar(tmp_path):
    """Factory: an OCI layout tar; keyword hooks bend one property to break one guard."""

    def build(
        layers=(b"layer-one",),
        config=b"{}",
        layer_extra=None,
        config_extra=None,
        layer_type=OCI_LAYER,
        entry_extra=None,
        corrupt=None,
        extra_entries=(),
        manifest_type=OCI_MANIFEST,
        name="image.tar",
    ) -> Path:
        manifest = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": manifest_type,
                "config": descriptor(config, OCI_CONFIG, **(config_extra or {})),
                "layers": [descriptor(b, layer_type, **(layer_extra or {})) for b in layers],
            }
        ).encode()
        entry = descriptor(manifest, manifest_type, **(entry_extra or {}))
        index = json.dumps({"manifests": [entry, *extra_entries]}).encode()
        members = {"index.json": index, "oci-layout": b'{"imageLayoutVersion":"1.0.0"}'}
        for data in (manifest, config, *layers):
            members["blobs/sha256/" + sha(data)[7:]] = data
        if corrupt is not None:
            members["blobs/sha256/" + sha(corrupt)[7:]] = b"tampered" + corrupt[8:]
        path = tmp_path / name
        with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as tf:
            for member, data in members.items():
                info = tarfile.TarInfo(member)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        return path

    return build


class FakeRegistry:
    """In-memory distribution API; `location` overrides the upload Location header."""

    def __init__(self, location="/v2/r/blobs/uploads/u1", have=()):
        self.location, self.have, self.requests = location, set(have), []
        self.stored, self.manifest = {}, None
        self.head_size, self.manifest_echo = None, None
        self.post_status, self.reply_body = 202, b""

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "HEAD":
            digest = path.rsplit("/", 1)[1]
            if digest in self.have:
                return httpx.Response(200, headers={"Content-Length": str(self.head_size)})
            return httpx.Response(404)
        if request.method == "POST":
            return httpx.Response(
                self.post_status, headers={"Location": self.location}, content=self.reply_body
            )
        body = await request.aread()
        if "/manifests/" in path:
            self.manifest = body
            echo = self.manifest_echo or path.rsplit("/", 1)[1]
            return httpx.Response(201, headers={"Docker-Content-Digest": echo})
        self.stored[request.url.params["digest"]] = body
        return httpx.Response(201)


@pytest.fixture
def registry():
    return FakeRegistry()


def pusher(registry) -> RegistryPusher:
    return RegistryPusher(BASE, "127.0.0.1:5000", transport=httpx.MockTransport(registry))


async def refused(coro, text: str = "") -> DeployRejectedError:
    with pytest.raises(DeployRejectedError) as caught:
        await coro
    assert text in caught.value.detail
    return caught.value


async def test_push_uploads_blobs_then_manifest_and_returns_digest_only_ref(image_tar, registry):
    tar = image_tar(layers=(b"layer-one", b"layer-two"))
    ref = await pusher(registry).push(tar, "a4-abc-1")
    manifest_digest = sha(registry.manifest)
    assert ref == f"127.0.0.1:5000/a4-abc-1@{manifest_digest}"
    assert set(registry.stored) == {sha(b"{}"), sha(b"layer-one"), sha(b"layer-two")}
    assert registry.stored[sha(b"layer-two")] == b"layer-two"
    assert registry.requests[-1].url.path.endswith(f"/manifests/{manifest_digest}")
    assert ":" not in ref.rpartition("@")[0].partition("/")[2]  # no tag anywhere


async def test_existing_blob_is_skipped_by_digest_and_size(image_tar, registry):
    registry.have, registry.head_size = {sha(b"layer-one")}, len(b"layer-one")
    await pusher(registry).push(image_tar(), "r")
    assert sha(b"layer-one") not in registry.stored
    assert sha(b"{}") in registry.stored


async def test_existing_blob_with_wrong_size_is_uploaded_again(image_tar, registry):
    registry.have, registry.head_size = {sha(b"layer-one")}, 1
    await pusher(registry).push(image_tar(), "r")
    assert sha(b"layer-one") in registry.stored


@pytest.mark.parametrize(
    "location",
    [
        "http://evil.example/v2/r/blobs/uploads/u",
        "http://a4-registry:6000/v2/r/blobs/uploads/u",
        "https://a4-registry:5000/v2/r/blobs/uploads/u",
        "http://a4-registry.evil.example:5000/x",
        "http://a4-registry:5000@evil.example/x",
        "http://user:pw@a4-registry:5000/x",
        "//evil.example/x",
        "",
    ],
)
async def test_upload_location_outside_registry_origin_is_refused(image_tar, location):
    registry = FakeRegistry(location=location)
    await refused(pusher(registry).push(image_tar(), "r"), "location")
    assert [r.method for r in registry.requests] == ["HEAD", "POST"]


async def test_relative_and_same_origin_locations_are_followed(image_tar):
    registry = FakeRegistry(location=f"{BASE}/v2/r/blobs/uploads/u2?_state=abc")
    await pusher(registry).push(image_tar(), "r")
    puts = [r for r in registry.requests if r.method == "PUT" and "blobs" in r.url.path]
    assert puts and all(r.url.params["_state"] == "abc" for r in puts)


async def test_redirects_are_never_followed(image_tar):
    seen = []

    async def handler(request):
        seen.append(request.url.host)
        if request.url.host == "a4-registry":
            return httpx.Response(307, headers={"Location": "http://evil.example/steal"})
        return httpx.Response(200)

    push = RegistryPusher(BASE, "r", transport=httpx.MockTransport(handler))
    await refused(push.push(image_tar(), "r"), "redirect")
    assert seen == ["a4-registry"]


async def test_client_ignores_proxy_environment_and_sets_every_timeout():
    client = RegistryPusher(BASE, "r")._client()
    assert client.follow_redirects is False
    assert client._trust_env is False  # noqa: SLF001 (httpx exposes no public flag)
    assert all(
        0 < t <= 120
        for t in (
            client.timeout.connect,
            client.timeout.read,
            client.timeout.write,
            client.timeout.pool,
        )
    )


async def test_tampered_blob_is_refused_before_any_request(image_tar, registry):
    tar = image_tar(corrupt=b"layer-one")
    await refused(pusher(registry).push(tar, "r"), "does not match")
    assert registry.requests == []


async def test_descriptor_size_lie_is_refused_before_any_request(image_tar, registry):
    tar = image_tar(layer_extra={"size": 3})
    await refused(pusher(registry).push(tar, "r"), "lacks a blob")
    assert registry.requests == []


@pytest.mark.parametrize(
    ("kwargs", "text"),
    [
        ({"layer_extra": {"urls": ["http://evil.example/l"]}}, "layer descriptor"),
        ({"config_extra": {"urls": ["http://evil.example/c"]}}, "config descriptor"),
        ({"entry_extra": {"urls": ["http://evil.example/m"]}}, "manifest descriptor"),
        ({"layer_type": "application/vnd.oci.image.layer.nondistributable.v1.tar"}, "layer"),
        ({"layer_type": "application/vnd.in-toto+json"}, "layer"),
        ({"manifest_type": "application/vnd.oci.image.index.v1+json"}, "manifest descriptor"),
        (
            {"entry_extra": {"annotations": {"vnd.docker.reference.type": "attestation-manifest"}}},
            "attestation",
        ),
        (
            {
                "extra_entries": [
                    {"mediaType": OCI_MANIFEST, "digest": "sha256:" + "0" * 64, "size": 1}
                ]
            },
            "exactly one",
        ),
        ({"layers": tuple(b"l%d" % i for i in range(MAX_LAYERS + 1))}, "too many"),
        ({"config": b"{" + b" " * MAX_JSON_BYTES + b"}"}, "config is too large"),
    ],
)
async def test_unsafe_image_shapes_are_refused_before_any_request(
    image_tar, registry, kwargs, text
):
    await refused(pusher(registry).push(image_tar(**kwargs), "r"), text)
    assert registry.requests == []


async def test_layer_count_at_the_cap_is_accepted(image_tar, registry):
    layers = tuple(b"l%d" % i for i in range(MAX_LAYERS))
    await pusher(registry).push(image_tar(layers=layers), "r")
    assert len(registry.stored) == MAX_LAYERS + 1


async def test_oversized_manifest_is_refused(tmp_path, registry):
    big = b'{"schemaVersion":2,"layers":[],"pad":"' + b"x" * MAX_JSON_BYTES + b'"}'
    index = json.dumps({"manifests": [descriptor(big, OCI_MANIFEST)]}).encode()
    tar = tmp_path / "big.tar"
    with tarfile.open(tar, "w", format=tarfile.USTAR_FORMAT) as tf:
        for name, data in {"index.json": index, "blobs/sha256/" + sha(big)[7:]: big}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    await refused(pusher(registry).push(tar, "r"), "manifest is too large")


async def test_tar_with_a_symlink_member_is_refused_before_any_request(image_tar, registry):
    tar = image_tar()
    with tarfile.open(tar, "a", format=tarfile.USTAR_FORMAT) as tf:
        link = tarfile.TarInfo("blobs/sha256/" + "a" * 64)
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tf.addfile(link)
    await refused(pusher(registry).push(tar, "r"))
    assert registry.requests == []


@pytest.mark.parametrize("repo", ["", "A", "../x", "a/b", "a:latest", "a@sha256:x", "x" * 129])
async def test_repository_name_is_validated(image_tar, registry, repo):
    await refused(pusher(registry).push(image_tar(), repo), "repository")
    assert registry.requests == []


async def test_oversized_registry_reply_is_refused(image_tar):
    registry = FakeRegistry()
    registry.post_status, registry.reply_body = 500, b"x" * (MAX_REPLY_BYTES + 1)
    await refused(pusher(registry).push(image_tar(), "r"), "too large")


async def test_server_text_is_redacted_in_the_failure_detail(image_tar):
    registry = FakeRegistry()
    registry.post_status = 500
    registry.reply_body = b"Authorization: Bearer abcdef0123456789 token=hunter2secret"
    error = await refused(pusher(registry).push(image_tar(), "r"), "registry answered 500")
    assert "hunter2secret" not in error.detail
    assert "abcdef0123456789" not in error.detail


async def test_head_redirect_is_a_refusal_not_a_miss(image_tar):
    async def handler(request):
        return httpx.Response(302, headers={"Location": "/elsewhere"})

    push = RegistryPusher(BASE, "r", transport=httpx.MockTransport(handler))
    await refused(push.push(image_tar(), "r"), "redirect")


async def test_manifest_digest_echo_mismatch_is_refused(image_tar, registry):
    registry.manifest_echo = "sha256:" + "0" * 64
    await refused(pusher(registry).push(image_tar(), "r"), "different manifest digest")


async def test_timeout_becomes_a_tool_failure(image_tar):
    async def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    push = RegistryPusher(BASE, "r", transport=httpx.MockTransport(handler))
    error = await refused(push.push(image_tar(), "r"), "unreachable")
    assert error.reason_code == "tool_failed"


@pytest.fixture
def build_result():
    def build(tar: Path, digest: str | None = None) -> BuildResult:
        with tarfile.open(tar) as tf:
            index = tf.extractfile("index.json")
            assert index is not None
            manifest = json.loads(index.read())["manifests"][0]["digest"]
        return BuildResult(f"{tar}@{digest or manifest}", 7, artifact=tar)

    return build


async def test_pushing_builder_swaps_tarball_for_a_registry_ref(
    image_tar, build_result, registry, tmp_path
):
    tar = image_tar()
    inner = FakeBuilder(result=build_result(tar))
    out = await PushingBuilder(inner, pusher(registry)).build(
        BuildRequest(context_dir=tmp_path, tag="a4-abc-1:0123456789ab")
    )
    assert out.artifact is None
    assert out.image_ref == f"127.0.0.1:5000/a4-abc-1@{sha(registry.manifest)}"
    assert out.image_bytes == 7
    assert not tar.exists()


async def test_pushing_builder_deletes_the_tarball_when_the_push_fails(
    image_tar, build_result, tmp_path
):
    tar = image_tar(corrupt=b"layer-one")
    builder = PushingBuilder(FakeBuilder(result=build_result(tar)), pusher(FakeRegistry()))
    await refused(builder.build(BuildRequest(context_dir=tmp_path, tag="r:t")))
    assert not tar.exists()


async def test_pushing_builder_refuses_a_digest_that_differs_from_the_build(
    image_tar, build_result, registry, tmp_path
):
    tar = image_tar()
    builder = PushingBuilder(
        FakeBuilder(result=build_result(tar, "sha256:" + "9" * 64)), pusher(registry)
    )
    await refused(builder.build(BuildRequest(context_dir=tmp_path, tag="r:t")), "differs")
    assert not tar.exists()


async def test_pushing_builder_needs_a_tarball(registry, tmp_path):
    builder = PushingBuilder(FakeBuilder(), pusher(registry))
    await refused(builder.build(BuildRequest(context_dir=tmp_path, tag="r:t")), "no image tarball")


def test_registry_url_with_credentials_or_https_is_refused_at_start(tmp_path):
    for sub in ("tls", "out"):
        (tmp_path / sub).mkdir()
    for name in ("ca.pem", "client.pem", "client-key.pem"):
        (tmp_path / "tls" / name).write_text("pem")
    for url in ("http://u:p@a4-registry:5000", "https://a4-registry:5000", "a4-registry:5000"):
        cfg = Settings(
            _env_file=None,
            a4_builder="buildkit",
            a4_registry_url=url,
            a4_buildkit_tls_dir=tmp_path / "tls",
            a4_builder_out_dir=tmp_path / "out",
        )
        with pytest.raises(RuntimeError, match="A4_REGISTRY_URL"):
            builder_from_settings(cfg)

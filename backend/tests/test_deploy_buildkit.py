"""A4 P3b-1: BuildKitBuilder argv, limits and refusals against a stub `buildctl`."""

import json
import os
import sys

import pytest

from app.core.config import Settings
from app.services.deployment.builder import Builder, BuildRequest
from app.services.deployment.buildkit import (
    BuildKitBuilder,
    builder_from_settings,
    validate_builder_settings,
)
from app.services.deployment.errors import DeployRejectedError

DIGEST = "sha256:" + "ab" * 32

STUB = f"""#!{sys.executable}
import json, pathlib, sys
here = pathlib.Path(__file__).parent
mode = json.loads((here / "mode.json").read_text())
(here / "argv.json").write_text(json.dumps(sys.argv[1:]))
args = sys.argv[1:]
dest = next(a for a in args if a.startswith("type=oci,dest=")).split("dest=", 1)[1]
meta = args[args.index("--metadata-file") + 1]
if mode.get("sleep"):
    import time; time.sleep(mode["sleep"])
if mode.get("tar_bytes") is not None:
    pathlib.Path(dest).write_bytes(b"x" * mode["tar_bytes"])
if mode.get("digest"):
    body = {{"containerimage.digest": mode["digest"], "pad": "p" * mode.get("meta_pad", 0)}}
    pathlib.Path(meta).write_text(json.dumps(body))
if mode.get("stdout"):
    print(mode["stdout"])
if mode.get("stderr"):
    print(mode["stderr"], file=sys.stderr)
sys.exit(mode.get("rc", 0))
"""


@pytest.fixture
def stub(tmp_path):
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    path = stub_dir / "buildctl"
    path.write_text(STUB)
    path.chmod(0o755)

    def configure(**mode):
        (stub_dir / "mode.json").write_text(json.dumps(mode))

    configure(tar_bytes=10, digest=DIGEST)
    return path, configure, stub_dir / "argv.json"


@pytest.fixture
def context(tmp_path):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    return ctx


@pytest.fixture
def builder(tmp_path, stub):
    out = tmp_path / "out"
    out.mkdir()
    return BuildKitBuilder(
        addr="tcp://10.213.77.5:1234",
        tls_dir=tmp_path / "tls",
        out_dir=out,
        buildctl=str(stub[0]),
        timeout=5,
        max_image_bytes=100,
    )


async def test_success_argv_and_result(builder, stub, context, tmp_path):
    assert isinstance(builder, Builder)
    result = await builder.build(BuildRequest(context_dir=context, tag="proj/app:abc123"))
    tar = tmp_path / "out" / "proj_app_abc123.tar"
    assert result.image_ref == f"{tar}@{DIGEST}"
    assert result.image_bytes == 10
    argv = json.loads(stub[2].read_text())
    tls = tmp_path / "tls"
    h = "10.213.77.12"
    assert argv == [
        "--addr", "tcp://10.213.77.5:1234",
        "--tlscacert", str(tls / "ca.pem"),
        "--tlscert", str(tls / "client.pem"),
        "--tlskey", str(tls / "client-key.pem"),
        "build",
        "--frontend", "dockerfile.v0",
        "--local", f"context={context}",
        "--local", f"dockerfile={context}",
        "--opt", "filename=Dockerfile",
        "--opt", f"build-arg:PIP_INDEX_URL=http://{h}:8081/simple/",
        "--opt", f"build-arg:PIP_TRUSTED_HOST={h}",
        "--opt", f"build-arg:npm_config_registry=http://{h}:8082/",
        "--opt", "build-arg:npm_config_audit=false",
        "--opt", f"build-arg:GOPROXY=http://{h}:8083",
        "--opt", f"build-arg:GOSUMDB=sum.golang.org http://{h}:8083/sumdb",
        "--output", f"type=oci,dest={tar}",
        "--metadata-file", str(tmp_path / "out" / "proj_app_abc123.json"),
    ]  # fmt: skip


async def test_mirror_host_is_the_only_address_in_build_args(stub, context, tmp_path):
    out = tmp_path / "out2"
    out.mkdir()
    custom = BuildKitBuilder(
        addr="tcp://10.213.77.5:1234",
        tls_dir=tmp_path / "tls",
        out_dir=out,
        mirror_host="10.9.8.7",
        buildctl=str(stub[0]),
        timeout=5,
    )
    await custom.build(BuildRequest(context_dir=context, tag="t"))
    argv = json.loads(stub[2].read_text())
    args = [a for a in argv if a.startswith("build-arg:")]
    assert len(args) == 6
    assert not [a for a in args if "CARGO" in a]
    assert not [a for a in args if "PROXY=" in a.upper().replace("GOPROXY=", "")]
    assert "10.213.77.12" not in " ".join(args)
    assert sum("10.9.8.7" in a for a in args) == 5


@pytest.mark.parametrize("host", ["10.213.77.12", "192.168.0.1", "172.16.5.9"])
def test_mirror_host_accepts_ipv4_literals(host):
    assert Settings(_env_file=None, a4_pkg_mirror_host=host).a4_pkg_mirror_host == host


@pytest.mark.parametrize(
    "host",
    [
        "pkg-mirror",
        "pkg.example.com",
        "::1",
        "fe80::1",
        "1.2.3.4:80",
        "",
        " 10.0.0.1",
        "10.0.0",
        "999.1.1.1",
        "10.0.0.1/8",
        "010.0.0.1",
        "127.0.0.1",
        "0.0.0.0",
        "169.254.1.1",
        "8.8.8.8",
        "255.255.255.255",
    ],
)
def test_mirror_host_rejects_everything_but_an_ipv4_literal(host):
    with pytest.raises(ValueError):
        Settings(_env_file=None, a4_pkg_mirror_host=host)


def test_proxy_setting_is_gone():
    assert not hasattr(Settings(_env_file=None), "a4_builder_proxy_url")


async def test_never_passes_secret_ssh_or_entitlements(builder, stub, context):
    await builder.build(BuildRequest(context_dir=context, tag="t"))
    joined = " ".join(json.loads(stub[2].read_text()))
    for flag in ("--secret", "--ssh", "--allow", "--registry", "auth", "network=host"):
        assert flag not in joined


@pytest.mark.parametrize(
    "name", ["../x", "/abs", "a/b", "..", "", "x\0y", "-x", "a,b", "a=b", " x"]
)
async def test_bad_dockerfile_name_rejected(builder, stub, context, name):
    with pytest.raises(DeployRejectedError) as exc:
        await builder.build(BuildRequest(context_dir=context, tag="t", dockerfile=name))
    assert exc.value.reason_code == "manifest_invalid"
    assert not stub[2].exists()


@pytest.mark.parametrize("tag", ["", ".hidden", "..", "a" * 200])
async def test_bad_tag_rejected(builder, stub, context, tag):
    with pytest.raises(DeployRejectedError) as exc:
        await builder.build(BuildRequest(context_dir=context, tag=tag))
    assert exc.value.reason_code == "manifest_invalid"
    assert not stub[2].exists()


async def test_oversize_removes_tar(builder, stub, context, tmp_path):
    stub[1](tar_bytes=101, digest=DIGEST)
    with pytest.raises(DeployRejectedError) as exc:
        await builder.build(BuildRequest(context_dir=context, tag="t"))
    assert exc.value.reason_code == "image_too_large"
    assert list((tmp_path / "out").iterdir()) == []


async def test_exactly_max_is_allowed(builder, stub, context):
    stub[1](tar_bytes=100, digest=DIGEST)
    assert (await builder.build(BuildRequest(context_dir=context, tag="t"))).image_bytes == 100


async def test_nonzero_rc_is_build_failed_redacted_and_cleans(builder, stub, context, tmp_path):
    stub[1](tar_bytes=5, rc=1, stdout="pull https://user:hunter2@evil.example/x failed")
    with pytest.raises(DeployRejectedError) as exc:
        await builder.build(BuildRequest(context_dir=context, tag="t"))
    assert exc.value.reason_code == "build_failed"
    assert "hunter2" not in str(exc.value)
    assert list((tmp_path / "out").iterdir()) == []


async def test_missing_metadata_is_build_failed(builder, stub, context, tmp_path):
    stub[1](tar_bytes=5)
    with pytest.raises(DeployRejectedError) as exc:
        await builder.build(BuildRequest(context_dir=context, tag="t"))
    assert exc.value.reason_code == "build_failed"
    assert list((tmp_path / "out").iterdir()) == []


async def test_bad_digest_is_build_failed(builder, stub, context):
    stub[1](tar_bytes=5, digest="nonsense")
    with pytest.raises(DeployRejectedError) as exc:
        await builder.build(BuildRequest(context_dir=context, tag="t"))
    assert exc.value.reason_code == "build_failed"


async def test_timeout_is_tool_failed_and_cleans(builder, stub, context, tmp_path):
    stub[1](tar_bytes=5, digest=DIGEST, sleep=30)
    builder.timeout = 0.5
    with pytest.raises(DeployRejectedError) as exc:
        await builder.build(BuildRequest(context_dir=context, tag="t"))
    assert exc.value.reason_code == "tool_failed"
    assert list((tmp_path / "out").iterdir()) == []


async def test_credential_url_straddling_tail_cut_is_redacted(builder, stub, context):
    noise = "x" * 10 + " https://user:SECRETTOKEN@h/x " + "y" * 283
    stub[1](tar_bytes=5, rc=1, stdout=noise)
    with pytest.raises(DeployRejectedError) as exc:
        await builder.build(BuildRequest(context_dir=context, tag="t"))
    assert "SECRETTOKEN" not in str(exc.value)


async def test_oversized_metadata_is_build_failed(builder, stub, context, tmp_path):
    stub[1](tar_bytes=5, digest=DIGEST, meta_pad=70 * 1024)
    with pytest.raises(DeployRejectedError) as exc:
        await builder.build(BuildRequest(context_dir=context, tag="t"))
    assert exc.value.reason_code == "build_failed"
    assert list((tmp_path / "out").iterdir()) == []


async def test_failure_detail_shows_redacted_stderr(builder, stub, context):
    _, configure, _ = stub
    configure(rc=1, stderr="error: pull https://bot:s3cret@mirror.local/v2 denied")
    with pytest.raises(DeployRejectedError) as exc:
        await builder.build(BuildRequest(context_dir=context, tag="t"))
    assert exc.value.reason_code == "build_failed"
    assert "denied" in exc.value.detail and "s3cret" not in exc.value.detail


async def test_gosumdb_is_one_argv_element(builder, stub, context):
    _, _, argv_file = stub
    await builder.build(BuildRequest(context_dir=context, tag="t"))
    argv = json.loads(argv_file.read_text())
    gosumdb = [a for a in argv if a.startswith("build-arg:GOSUMDB=")]
    assert gosumdb == ["build-arg:GOSUMDB=sum.golang.org http://10.213.77.12:8083/sumdb"]
    assert argv[argv.index(gosumdb[0]) - 1] == "--opt"


# A4 P3b-2c: `A4_BUILDER=buildkit` is validated when the worker starts, not at the first build.
TLS_FILES = ("ca.pem", "client.pem", "client-key.pem")


@pytest.fixture
def ready(tmp_path):
    tls = tmp_path / "tls"
    tls.mkdir()
    for name in TLS_FILES:
        (tls / name).write_text("pem")
    out = tmp_path / "out"
    out.mkdir()
    return dict(
        _env_file=None,
        a4_builder="buildkit",
        a4_buildkit_tls_dir=tls,
        a4_builder_out_dir=out,
    )


def test_valid_settings_pass_and_build_the_builder(ready):
    validate_builder_settings(Settings(**ready))
    assert builder_from_settings(Settings(**ready)) is not None


def test_fake_builder_skips_validation(tmp_path):
    cfg = Settings(_env_file=None, a4_buildkit_tls_dir=tmp_path / "missing")
    validate_builder_settings(cfg)


@pytest.mark.parametrize(
    "addr",
    [
        "buildkitd:1234",
        "http://buildkitd:1234",
        "tcp://buildkitd",
        "tcp://:1",
        "tcp://h:abc",
        "tcp://h:99999",
    ],
)
def test_bad_addr_is_refused(ready, addr):
    with pytest.raises(RuntimeError, match="A4_BUILDKIT_ADDR"):
        validate_builder_settings(Settings(**ready, a4_buildkit_addr=addr))


@pytest.mark.parametrize("name", TLS_FILES)
def test_missing_tls_file_is_named_without_contents(ready, name):
    (ready["a4_buildkit_tls_dir"] / name).unlink()
    with pytest.raises(RuntimeError) as exc:
        builder_from_settings(Settings(**ready))
    assert name in str(exc.value) and "pem" not in str(exc.value).replace(name, "")


def test_unwritable_out_dir_is_refused(ready, tmp_path):
    ready["a4_builder_out_dir"] = tmp_path / "absent"
    with pytest.raises(RuntimeError, match="A4_BUILDER_OUT_DIR"):
        validate_builder_settings(Settings(**ready))


async def test_failure_detail_keeps_only_the_last_300_characters(builder, stub, context):
    _, configure, _ = stub
    configure(rc=1, stderr="a" * 5000 + "END-MARK")
    with pytest.raises(DeployRejectedError) as exc:
        await builder.build(BuildRequest(context_dir=context, tag="t"))
    prefix = "buildctl rc=1: "
    assert len(exc.value.detail) == len(prefix) + 300
    assert exc.value.detail.endswith("END-MARK")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
class TestPermissionChecks:
    def test_read_only_out_dir_is_refused(self, ready):
        out = ready["a4_builder_out_dir"]
        out.chmod(0o555)
        try:
            with pytest.raises(RuntimeError, match="A4_BUILDER_OUT_DIR"):
                validate_builder_settings(Settings(**ready))
        finally:
            out.chmod(0o755)

    def test_unreadable_tls_file_is_refused_by_name(self, ready):
        key = ready["a4_buildkit_tls_dir"] / "client-key.pem"
        key.chmod(0o000)
        try:
            with pytest.raises(RuntimeError, match="client-key.pem"):
                validate_builder_settings(Settings(**ready))
        finally:
            key.chmod(0o600)

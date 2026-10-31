"""A4 P2a: fetcher - GitHub tarball of an exact sha, streamed cap, safe extraction, stripping."""

import asyncio
import io
import tarfile
import threading
import time

import httpx
import pytest

from app.services.deployment import fetcher
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.fetcher import FetchPolicy, fetch_source, is_stripped

SHA = "a" * 40
REPO = "https://github.com/AgentSpore/demo-app"
TOP = f"AgentSpore-demo-app-{SHA[:7]}"


@pytest.fixture
def make_tarball():
    """Gzip tarball from (name, kind, payload); kind is file|dir|symlink|hardlink|device."""

    def build(entries, prefix=TOP):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for name, kind, payload in entries:
                info = tarfile.TarInfo(
                    name if name.startswith("/") else f"{prefix}/{name}".strip("/")
                )
                if kind == "dir":
                    info.type = tarfile.DIRTYPE
                elif kind == "file":
                    data = payload.encode() if isinstance(payload, str) else payload
                    info.size = len(data)
                elif kind in {"symlink", "hardlink"}:
                    info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                    info.linkname = payload
                else:
                    info.type = tarfile.CHRTYPE
                tar.addfile(info, io.BytesIO(data) if kind == "file" else None)
        return buffer.getvalue()

    return build


@pytest.fixture
def serve():
    """Client whose transport answers every request with `body`; `.seen` lists requested URLs."""

    def build(body=b"", status=200):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(status, content=body)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client.seen = seen
        return client

    return build


async def test_fetcher_unpacks_below_top_dir_and_strips_secret_files(make_tarball, serve, tmp_path):
    body = make_tarball(
        [
            ("Dockerfile", "file", "FROM scratch\n"),
            ("app", "dir", None),
            ("app/main.py", "file", "print(1)\n"),
            ("src/id_generator.py", "file", "x"),
            ("app/id_utils.py", "file", "x"),
            (".env.example", "file", "A=1"),
            (".env.sample", "file", "A=1"),
            (".env", "file", "S=1"),
            (".env.production", "file", "S=2"),
            ("deploy.pem", "file", "k"),
            ("nested/tls.key", "file", "k"),
            ("id_rsa", "file", "k"),
            ("id_ed25519", "file", "k"),
            (".gitleaks.toml", "file", "x"),
            (".gitleaksignore", "file", "x"),
            ("sub/osv-scanner.toml", "file", "x"),
        ]
    )
    client = serve(body)
    dest = tmp_path / "dest"
    await fetch_source(REPO, SHA, dest, client=client)
    names = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    assert names == {
        "Dockerfile",
        "app/main.py",
        "src/id_generator.py",
        "app/id_utils.py",
        ".env.example",
        ".env.sample",
    }
    assert client.seen == [f"https://codeload.github.com/AgentSpore/demo-app/tar.gz/{SHA}"]


@pytest.mark.parametrize(
    ("name", "stripped"),
    [("id_rsa", True), ("id_dsa", True), (".env", True), (".env.local", True), ("a.pem", True),
     ("id_generator.py", False), ("id_utils.py", False), (".env.example", False), (".envrc", False),
     ("environment.py", False), ("monkey.key.py", False), ("DEPLOY.PEM", True), ("tls.KEY", True),
     ("ID_RSA", True), (".ENV", True), (".Env.Local", True), (".ENV.example", False)],
)  # fmt: skip
def test_is_stripped_is_exact(name, stripped):
    assert is_stripped(name) is stripped


@pytest.mark.parametrize(
    "entry",
    [
        ("hard", "hardlink", "real.txt"),
        ("dev", "device", None),
        ("../outside.txt", "file", "x"),
        ("a/../../outside.txt", "file", "x"),
        ("/abs/outside.txt", "file", "x"),
    ],
)
async def test_fetcher_rejects_hardlinks_devices_and_escaping_paths(
    make_tarball, serve, tmp_path, entry
):
    body = make_tarball([("real.txt", "file", "x"), entry])
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(REPO, SHA, tmp_path / "dest", client=serve(body))
    assert exc.value.reason_code == "source_unsafe"
    assert not (tmp_path / "dest").exists()
    assert not (tmp_path / "outside.txt").exists()


async def test_fetcher_enforces_unpacked_file_and_size_caps(make_tarball, serve, tmp_path):
    many = make_tarball([(f"f{i}.txt", "file", "x") for i in range(5)])
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(REPO, SHA, tmp_path / "d1", FetchPolicy(max_files=4), serve(many))
    assert exc.value.reason_code == "source_too_large"
    big = make_tarball([("big.bin", "file", b"0" * 2048)])
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(REPO, SHA, tmp_path / "d2", FetchPolicy(max_bytes=1024), serve(big))
    assert exc.value.reason_code == "source_too_large"
    assert not (tmp_path / "d1").exists()
    assert not (tmp_path / "d2").exists()


async def test_fetcher_drops_symlinks_and_deploys_the_rest(make_tarball, serve, tmp_path):
    body = make_tarball(
        [
            ("real.txt", "file", "x"),
            ("leak", "symlink", "/etc/passwd"),
            ("inside", "symlink", "real.txt"),
        ]
    )
    dest = tmp_path / "dest"
    await fetch_source(REPO, SHA, dest, client=serve(body))
    assert sorted(p.name for p in dest.iterdir()) == ["real.txt"]


async def test_fetcher_counts_skipped_members_toward_the_caps(make_tarball, serve, tmp_path):
    many = make_tarball([(f".env.{i}", "file", "S") for i in range(5)])
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(REPO, SHA, tmp_path / "d1", FetchPolicy(max_files=4), serve(many))
    assert exc.value.reason_code == "source_too_large"
    big = make_tarball([("tls.pem", "file", b"0" * 2048)])
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(REPO, SHA, tmp_path / "d2", FetchPolicy(max_bytes=1024), serve(big))
    assert exc.value.reason_code == "source_too_large"
    top = make_tarball([(f"top{i}", "file", "x") for i in range(3)], prefix="")
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(REPO, SHA, tmp_path / "d3", FetchPolicy(max_files=2), serve(top))
    assert exc.value.reason_code == "source_too_large"


async def test_fetcher_extraction_has_a_deadline(make_tarball, serve, tmp_path):
    body = make_tarball([("a.txt", "file", "x")])
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(
            REPO, SHA, tmp_path / "dest", FetchPolicy(extract_timeout=-1), serve(body)
        )
    assert exc.value.reason_code == "source_fetch_failed"
    assert not (tmp_path / "dest").exists()


async def test_fetcher_stops_reading_at_archive_cap(tmp_path):
    sent = []

    async def body():
        for _ in range(100):
            sent.append(1)
            yield b"x" * 65536

    def handler(request):
        return httpx.Response(200, content=body())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(REPO, SHA, tmp_path / "dest", FetchPolicy(max_archive=200_000), client)
    assert exc.value.reason_code == "source_too_large"
    assert len(sent) < 10


async def test_fetcher_http_failures_are_fetch_failures(serve, tmp_path):
    for status in (404, 302, 500):
        with pytest.raises(DeployRejectedError) as exc:
            await fetch_source(REPO, SHA, tmp_path / f"d{status}", client=serve(b"", status))
        assert exc.value.reason_code == "source_fetch_failed"
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(REPO, SHA, tmp_path / "garbage", client=serve(b"not a tarball"))
    assert exc.value.reason_code == "source_fetch_failed"

    def refuse(request):
        raise httpx.ConnectError("boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(REPO, SHA, tmp_path / "refused", client=client)
    assert exc.value.reason_code == "source_fetch_failed"
    assert not (tmp_path / "refused").exists()


async def test_fetcher_download_timeout(tmp_path):
    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(REPO, SHA, tmp_path / "dest", FetchPolicy(timeout=0.05), client)
    assert exc.value.reason_code == "source_fetch_failed"
    assert not (tmp_path / "dest").exists()


@pytest.mark.parametrize(
    ("url", "sha"),
    [
        ("https://user:token@github.com/o/r", SHA),
        ("http://github.com/o/r", SHA),
        ("ssh://git@github.com/o/r", SHA),
        ("https://gitlab.com/o/r", SHA),
        ("https://github.com.evil.example/o/r", SHA),
        ("https://github.com:444/o/r", SHA),
        ("https://github.com:abc/o/r", SHA),
        ("https://github.com:99999/o/r", SHA),
        ("https://github.com/../r", SHA),
        ("https://github.com/o/..", SHA),
        ("https://github.com/./r", SHA),
        ("https://github.com/o", SHA),
        ("https://github.com/o/r/tree/main", SHA),
        ("https://github.com/o/r?x=1", SHA),
        ("https://github.com/o/r", "main"),
        ("https://github.com/o/r", "A" * 40),
        ("https://github.com/o/r", "a" * 39),
    ],
)
async def test_fetcher_rejects_bad_inputs_without_a_request(tmp_path, url, sha):
    def forbidden(request):
        raise AssertionError("no request may be made")

    client = httpx.AsyncClient(transport=httpx.MockTransport(forbidden))
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(url, sha, tmp_path / "dest", client=client)
    assert exc.value.reason_code == "source_invalid"
    assert not (tmp_path / "dest").exists()


async def test_fetcher_refuses_oversized_pax_headers(serve, tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        info = tarfile.TarInfo(f"{TOP}/a.txt")
        info.pax_headers = {"comment": "x" * 4_000_000}
        tar.addfile(info, io.BytesIO(b""))
    policy = FetchPolicy(max_bytes=1024, max_files=10)
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(REPO, SHA, tmp_path / "dest", policy, serve(buffer.getvalue()))
    assert exc.value.reason_code == "source_too_large"
    assert not (tmp_path / "dest").exists()


async def test_fetcher_cancel_waits_for_the_extraction_thread(
    make_tarball, serve, tmp_path, monkeypatch
):
    started, writes = threading.Event(), []
    real_unpack = fetcher._unpack

    def slow_unpack(tar, member, dest):
        writes.append(member.name)
        started.set()
        time.sleep(0.2)
        return real_unpack(tar, member, dest)

    monkeypatch.setattr(fetcher, "_unpack", slow_unpack)
    body = make_tarball([(f"f{i}.txt", "file", "x") for i in range(30)])
    dest = tmp_path / "dest"
    task = asyncio.create_task(fetch_source(REPO, SHA, dest, client=serve(body)))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    seen = len(writes)
    await asyncio.sleep(0.6)
    assert not dest.exists()
    assert len(writes) == seen

"""Exercise installer integrity and architecture boundaries with synthetic release bytes."""

import hashlib
import io
import tarfile
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import install_a4_scanners as installer


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_install_target_and_permissions(monkeypatch, tmp_path: Path, architecture):
    suffix, machine, _, _ = installer.PINS[architecture]
    elf = bytearray(64)
    elf[:6] = b"\x7fELF\x02\x01"
    elf[18:20] = machine.to_bytes(2, "little")
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w:gz") as archive:
        for name, data in [("gitleaks", bytes(elf)), ("LICENSE", b"license")]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    urls = []

    def download(url, expected):
        urls.append(url)
        if url.endswith(".tar.gz"):
            return archive_bytes.getvalue()
        return b"license" if url.endswith("LICENSE") else bytes(elf)

    monkeypatch.setattr(installer, "verified_download", download)
    installer.install(architecture, tmp_path)
    assert f"linux_{suffix}.tar.gz" in urls[0]
    assert urls[1].endswith(f"linux_{architecture}")
    for name in ["gitleaks", "osv-scanner"]:
        binary = tmp_path / "bin" / name
        assert binary.read_bytes() == elf
        assert binary.stat().st_mode & 0o777 == 0o755
        assert (tmp_path / "share/licenses" / name / "LICENSE").read_bytes() == b"license"


def test_unsupported_target_has_no_side_effects(tmp_path):
    with pytest.raises(ValueError, match="scanner_architecture_unsupported"):
        installer.install("riscv64", tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("corrupt", [False, True])
def test_download_checksum(monkeypatch, corrupt):
    class Response(io.BytesIO):
        url = "https://example.invalid/public-release"

    response = Response(b"release bytes")
    monkeypatch.setattr(
        installer.urllib.request,
        "build_opener",
        lambda *a: SimpleNamespace(open=lambda *a, **kw: response),
    )
    expected = hashlib.sha256(b"release bytes").hexdigest()
    if corrupt:
        with pytest.raises(ValueError, match="scanner_checksum_mismatch"):
            installer.verified_download(response.url, "0" * 64)
    else:
        assert installer.verified_download(response.url, expected) == b"release bytes"


@pytest.mark.parametrize("kind", ["symlink", "duplicate", "traversal"])
def test_archive_rejects_unsafe_member(kind):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("../gitleaks" if kind == "traversal" else "gitleaks")
        if kind == "symlink":
            info.type = tarfile.SYMTYPE
            info.linkname = "/outside"
        archive.addfile(info)
        if kind == "duplicate":
            archive.addfile(info)
    with pytest.raises(ValueError, match="scanner_archive_member"):
        installer.archive_member(buffer.getvalue(), "gitleaks")


@pytest.mark.parametrize("data,machine", [(b"not ELF", 62), (b"\x7fELF\x02\x01" + bytes(58), 62)])
def test_invalid_elf_rejected(data, machine):
    with pytest.raises(ValueError, match="scanner_(elf_invalid|architecture_mismatch)"):
        installer.check_elf(data, machine)


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("header", ["location", "uri"])
@pytest.mark.parametrize(
    "target,allowed",
    [
        ("http://example.invalid/release", False),
        ("file:///tmp/release", False),
        ("https://release-assets.githubusercontent.com/release", True),
        ("/relative-release", True),
    ],
)
def test_redirect_transport_checked_before_follow(monkeypatch, code, header, target, allowed):
    followed = []

    def follow(self, req, fp, status, msg, headers):
        followed.append(headers[header])

    monkeypatch.setattr(urllib.request.HTTPRedirectHandler, "http_error_302", follow)
    handler = installer.HTTPSRedirectHandler()
    request = urllib.request.Request("https://github.com/public-release")
    redirect = getattr(handler, f"http_error_{code}")
    if allowed:
        redirect(request, None, code, "Found", {header: target})
        assert followed == [target]
    else:
        with pytest.raises(ValueError, match="scanner_download_transport"):
            redirect(request, None, code, "Found", {header: target})
        assert followed == []


def test_failed_verification_installs_nothing(monkeypatch, tmp_path):
    def corrupt_download(url, expected):
        raise ValueError("scanner_checksum_mismatch")

    monkeypatch.setattr(installer, "verified_download", corrupt_download)
    with pytest.raises(ValueError, match="scanner_checksum_mismatch"):
        installer.install("amd64", tmp_path)
    assert list(tmp_path.iterdir()) == []

"""Install reviewed scanner release bytes at image build time; never resolve latest."""

import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

PINS = {
    "amd64": (
        "x64",
        62,
        "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
        "160c6253e2922e9e0a6a9771c5c4c40a4b3c6090b34a3cdd5e80c96cc2e4218b",
    ),
    "arm64": (
        "arm64",
        183,
        "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080",
        "4061ef4c021ed3c47f203b818f337e351336cc5cd67c0274fc36bd1dfc728dd7",
    ),
}
MAX_BYTES = 256 * 1024 * 1024


class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject transport downgrades before urllib follows a release asset redirect."""

    def http_error_302(self, req, fp, code, msg, headers):
        target = headers.get("location", headers.get("uri"))
        if target is not None and urlsplit(urljoin(req.full_url, target)).scheme != "https":
            raise ValueError("scanner_download_transport")
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


def verified_download(url: str, expected: str) -> bytes:
    """Download bounded HTTPS content and verify its reviewed SHA-256 before use."""
    if not url.startswith("https://"):
        raise ValueError("scanner_download_transport")
    opener = urllib.request.build_opener(HTTPSRedirectHandler())
    with opener.open(url, timeout=60) as response:
        if not response.url.startswith("https://"):
            raise ValueError("scanner_download_transport")
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("scanner_checksum_mismatch")
    return data


def archive_member(data: bytes, name: str) -> bytes:
    """Read one uniquely named regular member without extracting archive paths."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = [member for member in archive.getmembers() if member.name == name]
        if len(members) != 1 or not members[0].isfile() or members[0].size > MAX_BYTES:
            raise ValueError("scanner_archive_member")
        stream = archive.extractfile(members[0])
        if stream is None:
            raise ValueError("scanner_archive_member")
        return stream.read(MAX_BYTES + 1)


def check_elf(data: bytes, machine: int) -> None:
    """Require a little-endian 64-bit ELF for the selected Linux target."""
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("scanner_elf_invalid")
    if int.from_bytes(data[18:20], "little") != machine:
        raise ValueError("scanner_architecture_mismatch")


def install(architecture: str, prefix: Path = Path("/usr/local")) -> None:
    """Verify both tools and licenses before installing executable image files."""
    if architecture not in PINS:
        raise ValueError("scanner_architecture_unsupported")
    suffix, machine, gitleaks_sha, osv_sha = PINS[architecture]
    archive = verified_download(
        "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/"
        f"gitleaks_8.30.1_linux_{suffix}.tar.gz",
        gitleaks_sha,
    )
    gitleaks = archive_member(archive, "gitleaks")
    gitleaks_license = archive_member(archive, "LICENSE")
    osv = verified_download(
        "https://github.com/google/osv-scanner/releases/download/v2.7.0/"
        f"osv-scanner_linux_{architecture}",
        osv_sha,
    )
    osv_license = verified_download(
        "https://raw.githubusercontent.com/google/osv-scanner/v2.7.0/LICENSE",
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    )
    for data in (gitleaks, osv):
        check_elf(data, machine)
    for name, data, license_data in (
        ("gitleaks", gitleaks, gitleaks_license),
        ("osv-scanner", osv, osv_license),
    ):
        binary = prefix / "bin" / name
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(data)
        binary.chmod(0o755)
        license_path = prefix / "share" / "licenses" / name / "LICENSE"
        license_path.parent.mkdir(parents=True, exist_ok=True)
        license_path.write_bytes(license_data)
        license_path.chmod(0o644)


if __name__ == "__main__":
    install(sys.argv[1])

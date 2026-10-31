"""A4 P2a: fetcher - exact-sha shallow fetch, caps, symlink refusal, secret-file stripping."""

import os
import subprocess

import pytest

from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.fetcher import FetchPolicy, fetch_source

POLICY = FetchPolicy(schemes=("file",), timeout=60)


@pytest.fixture
def make_repo(tmp_path):
    """Build a one-commit git repo from {path: bytes|str} plus {link: target}; return (url, sha)."""
    counter = iter(range(1000))

    def build(files, symlinks=None):
        repo = tmp_path / f"origin{next(counter)}"
        repo.mkdir()
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        for name, data in files.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data if isinstance(data, bytes) else data.encode())
        for link, dest in (symlinks or {}).items():
            os.symlink(dest, repo / link)
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "c"]):
            subprocess.run(["git", *cmd], cwd=repo, env=env, check=True, capture_output=True)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()
        return f"file://{repo}", sha

    return build


async def test_fetcher_checks_out_exact_sha_and_strips_secret_files(make_repo, tmp_path):
    url, sha = make_repo(
        {
            "Dockerfile": "FROM scratch\n",
            "app/main.py": "print(1)\n",
            ".env": "SECRET=1\n",
            ".env.production": "SECRET=2\n",
            "deploy.pem": "k",
            "tls.key": "k",
            "id_rsa": "k",
            "keep.txt": "ok",
        }
    )
    dest = tmp_path / "dest"
    await fetch_source(url, sha, dest, POLICY)
    names = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    assert names == {"Dockerfile", "app/main.py", "keep.txt"}
    assert not (dest / ".git").exists()


async def test_fetcher_rejects_symlink_escape(make_repo, tmp_path):
    url, sha = make_repo({"a.txt": "x"}, {"leak": "/etc/passwd"})
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(url, sha, tmp_path / "dest", POLICY)
    assert exc.value.reason_code == "source_unsafe"


async def test_fetcher_rejects_relative_symlink_escape(make_repo, tmp_path):
    url, sha = make_repo({"d/a.txt": "x"}, {"d/up": "../../outside"})
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(url, sha, tmp_path / "dest", POLICY)
    assert exc.value.reason_code == "source_unsafe"


async def test_fetcher_allows_symlink_inside_root(make_repo, tmp_path):
    url, sha = make_repo({"real.txt": "x"}, {"alias.txt": "real.txt"})
    await fetch_source(url, sha, tmp_path / "dest", POLICY)
    assert (tmp_path / "dest" / "alias.txt").is_symlink()


async def test_fetcher_enforces_file_and_size_caps(make_repo, tmp_path):
    url, sha = make_repo({f"f{i}.txt": "x" for i in range(5)})
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(url, sha, tmp_path / "d1", FetchPolicy(schemes=("file",), max_files=4))
    assert exc.value.reason_code == "source_too_large"
    url, sha = make_repo({"big.bin": b"0" * 2048})
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(
            url, sha, tmp_path / "d2", FetchPolicy(schemes=("file",), max_bytes=1024)
        )
    assert exc.value.reason_code == "source_too_large"
    assert not (tmp_path / "d2").exists() or not any((tmp_path / "d2").glob("big.bin"))


@pytest.mark.parametrize(
    ("url", "sha"),
    [
        ("https://user:token@github.com/o/r", "a" * 40),
        ("http://github.com/o/r", "a" * 40),
        ("ssh://git@github.com/o/r", "a" * 40),
        ("https://github.com/o/r", "main"),
        ("https://github.com/o/r", "A" * 40),
        ("https://github.com/o/r", "a" * 39),
        ("https://github.com/o/r", "--upload-pack=x"),
    ],
)
async def test_fetcher_rejects_bad_inputs_before_running_git(tmp_path, url, sha):
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(url, sha, tmp_path / "dest")
    assert exc.value.reason_code == "source_invalid"
    assert not (tmp_path / "dest").exists()


async def test_fetcher_unknown_sha_is_fetch_failure(make_repo, tmp_path):
    url, _ = make_repo({"a": "x"})
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(url, "b" * 40, tmp_path / "dest", POLICY)
    assert exc.value.reason_code == "source_fetch_failed"


async def test_fetcher_timeout_is_tool_failure(make_repo, tmp_path):
    url, sha = make_repo({"a": "x"})
    with pytest.raises(DeployRejectedError) as exc:
        await fetch_source(
            url, sha, tmp_path / "dest", FetchPolicy(schemes=("file",), timeout=0.0001)
        )
    assert exc.value.reason_code == "tool_failed"

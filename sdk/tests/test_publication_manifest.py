"""Publication manifests validate explicit text without filesystem access."""

from typing import Any

import pytest
from agentspore_sdk.publication_manifest import build_manifest


def test_known_git_blob_and_order_independence():
    manifest = build_manifest([{"path": "hello.txt", "content": "hello\n"}])
    assert (
        manifest["files"][0]["blob_sha"] == "ce013625030ba8dba906f756967f9e9ca394464a"
    )
    assert manifest["bytes"] == 6
    files = [
        {"path": "Dockerfile", "content": "FROM scratch\n"},
        {"path": "src/你好.py", "content": "你好"},
    ]
    assert build_manifest(files) == build_manifest(list(reversed(files)))


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/a",
        "../a",
        "a/../b",
        "a//b",
        "./a",
        "a/",
        "a\\b",
        "a\0b",
        "a\nb",
        ".git/config",
        ".SSH/key",
        ".aws/x",
        ".codex/x",
        ".claude/x",
        ".env",
        ".env.local",
        "x/credentials.json",
        "private_key",
        "private-key.pem",
        ".netrc",
        ".circleci/config.yml",
        "id_rsa",
        "x.pem",
        ".github/workflows/build.yml",
        ".gitlab-ci.yml",
        "x" * 201,
    ],
)
def test_unsafe_path_rejected(path):
    with pytest.raises(ValueError):
        build_manifest([{"path": path, "content": "body"}])


def test_unicode_and_transport_boundaries():
    files = [{"path": str(i), "content": "é" * 131072} for i in range(4)]
    assert build_manifest(files)["bytes"] == 1048576
    assert (
        len(
            build_manifest([{"path": str(i), "content": ""} for i in range(32)])[
                "files"
            ]
        )
        == 32
    )
    invalid_inputs: list[Any] = [
        [],
        files + [{"path": "extra", "content": "a"}],
        [{"path": "a", "content": "é" * 131073}],
        [{"path": str(i), "content": ""} for i in range(33)],
        [{"path": "a", "content": ""}] * 2,
        [{"path": "a", "content": "", "mode": "symlink"}],
        [{"path": "a", "content": b"binary"}],
    ]
    for invalid in invalid_inputs:
        with pytest.raises(ValueError):
            build_manifest(invalid)

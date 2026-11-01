"""Validate trusted policy and exercise the scanner built by the real worker factory."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.deployment.scans import DockerfileScanner, GitleaksScanner, OsvScanner
from app.workers import a4

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


def test_default_policy_is_empty(monkeypatch):
    monkeypatch.delenv("A4_BASE_IMAGE_DIGESTS", raising=False)
    assert Settings(_env_file=None).a4_base_image_digests == frozenset()


@pytest.mark.parametrize("digests", [[], [DIGEST], [DIGEST, OTHER_DIGEST, DIGEST]])
def test_policy_from_json_environment(monkeypatch, digests):
    monkeypatch.setenv("A4_BASE_IMAGE_DIGESTS", json.dumps(digests))
    assert Settings(_env_file=None).a4_base_image_digests == frozenset(digests)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-json",
        "null",
        "{}",
        '"sha256:abc"',
        "42",
        json.dumps([DIGEST, "sha256:" + "A" * 64]),
        json.dumps([DIGEST, "sha256:" + "g" * 64]),
        json.dumps([DIGEST, "sha256:" + "a" * 63]),
        json.dumps([DIGEST, "sha256:" + "a" * 65]),
        json.dumps([DIGEST, " " + DIGEST]),
        json.dumps([DIGEST, DIGEST + "\n"]),
        json.dumps([DIGEST, "python@" + DIGEST]),
        json.dumps([DIGEST, None]),
        json.dumps([DIGEST, 1]),
    ],
)
def test_invalid_policy_rejects_configuration(monkeypatch, value):
    monkeypatch.setenv("A4_BASE_IMAGE_DIGESTS", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("digests", "dockerfile", "accepted"),
    [
        ([DIGEST], f"FROM python@{DIGEST}\n", True),
        ([DIGEST], f"FROM python@{OTHER_DIGEST}\n", False),
        ([], f"FROM python@{DIGEST}\n", False),
        ([DIGEST], "FROM python:3.12-slim\n", False),
        ([], "FROM scratch AS build\nFROM build\n", True),
    ],
)
async def test_worker_factory_applies_policy(
    monkeypatch, tmp_path: Path, digests, dockerfile, accepted
):
    monkeypatch.setenv("A4_BASE_IMAGE_DIGESTS", json.dumps(digests))
    settings = Settings(_env_file=None, a4_builder="fake", a4_runtime="off")
    monkeypatch.setattr(a4, "get_settings", lambda: settings)
    task = a4.build_task()
    assert [type(scanner) for scanner in task.worker.scanners] == [
        GitleaksScanner,
        OsvScanner,
        DockerfileScanner,
    ]
    (tmp_path / "Dockerfile").write_text(dockerfile)
    findings = await task.worker.scanners[-1].scan(tmp_path)
    assert (findings == []) is accepted
    if not accepted:
        assert {finding.reason_code for finding in findings} == {"base_not_allowed"}

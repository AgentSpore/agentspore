"""A4 P2a: pre-build scans behind the Scanner interface, with fakes for the tool binaries.

Fixture shapes follow the real tools:
- gitleaks 8.30 `gitleaks dir . --report-format json --report-path -` (checked locally;
  exit 1 on leaks),
  report fields https://github.com/gitleaks/gitleaks#reporting
- osv-scanner v2 `scan source --format json`; exit 0 clean, 1 vulnerabilities found,
  128 no packages. JSON schema https://google.github.io/osv-scanner/output/
  (results[].packages[].vulnerabilities, groups[].max_severity); not run locally,
  osv-scanner is not installed here
"""

from pathlib import Path

import pytest
from loguru import logger

from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.scans import (
    DockerfileScanner,
    Finding,
    GitleaksScanner,
    OsvScanner,
    run_prescan,
)

FIXTURES = Path(__file__).parent / "fixtures" / "deploy"
DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


@pytest.fixture
def fake_run():
    """Factory for an injectable `run_tool` returning canned (rc, stdout) and recording argv."""

    def build(rc, stdout=b""):
        calls = []

        async def run(argv, *, cwd, timeout, env=None):
            calls.append((argv, cwd))
            return rc, stdout

        run.calls = calls
        return run

    return build


@pytest.fixture
def log_lines():
    lines = []
    sink = logger.add(lines.append, format="{message}")
    yield lines
    logger.remove(sink)


async def test_scan_secret_found_fails(fake_run, tmp_path, log_lines):
    run = fake_run(1, (FIXTURES / "gitleaks_report.json").read_bytes())
    with pytest.raises(DeployRejectedError) as exc:
        await run_prescan(tmp_path, [GitleaksScanner(run)])
    assert exc.value.reason_code == "secret_found"
    assert "github-pat" in exc.value.detail
    assert "app/settings.py:2" in exc.value.detail
    assert run.calls[0][0][:3] == ["gitleaks", "dir", "."]
    assert run.calls[0][1] == tmp_path
    logged = "".join(str(line) for line in log_lines) + str(exc.value)
    assert "github-pat" in logged
    assert "SENTINELSECRETVALUE" not in logged


async def test_gitleaks_clean_and_failure(fake_run, tmp_path):
    assert await GitleaksScanner(fake_run(0, b"[]")).scan(tmp_path) == []
    assert await GitleaksScanner(fake_run(0, b"")).scan(tmp_path) == []
    with pytest.raises(DeployRejectedError) as exc:
        await GitleaksScanner(fake_run(2)).scan(tmp_path)
    assert exc.value.reason_code == "tool_failed"
    with pytest.raises(DeployRejectedError) as exc:
        await GitleaksScanner(fake_run(1, b"not json")).scan(tmp_path)
    assert exc.value.reason_code == "tool_failed"


async def test_osv_only_critical_blocks(fake_run, tmp_path):
    findings = await OsvScanner(fake_run(1, (FIXTURES / "osv_report.json").read_bytes())).scan(
        tmp_path
    )
    by_id = {f.rule_id: f for f in findings}
    assert by_id["GHSA-crit-0001"].blocking is True
    assert by_id["GHSA-mod-0002"].blocking is False
    assert by_id["GHSA-crit-0001"].file == "requirements.txt"
    assert {f.reason_code for f in findings} == {"dependency_vulnerable"}


async def test_osv_warning_alone_does_not_block(fake_run, tmp_path):
    report = (FIXTURES / "osv_report.json").read_text().replace("9.8", "5.0").encode()
    await run_prescan(tmp_path, [OsvScanner(fake_run(1, report))])


async def test_osv_critical_fails_and_exit_codes(fake_run, tmp_path):
    with pytest.raises(DeployRejectedError) as exc:
        await run_prescan(
            tmp_path, [OsvScanner(fake_run(1, (FIXTURES / "osv_report.json").read_bytes()))]
        )
    assert exc.value.reason_code == "dependency_vulnerable"
    assert await OsvScanner(fake_run(128)).scan(tmp_path) == []
    assert await OsvScanner(fake_run(0, b'{"results": []}')).scan(tmp_path) == []
    with pytest.raises(DeployRejectedError) as exc:
        await OsvScanner(fake_run(127)).scan(tmp_path)
    assert exc.value.reason_code == "tool_failed"


def dockerfile_scanner(root: Path, body: str) -> DockerfileScanner:
    (root / "Dockerfile").write_text(body)
    return DockerfileScanner(frozenset({DIGEST}))


@pytest.mark.parametrize(
    "body",
    [
        f"FROM python:3.12@{DIGEST}\nCMD x\n",
        (
            f"FROM --platform=linux/amd64 python@{DIGEST} AS build\n"
            "FROM build AS final\nCOPY --from=build /a /b\n"
        ),
        "FROM scratch\nCOPY x /x\n",
        f"# comment\nFROM \\\n  python@{DIGEST}\n",
    ],
)
async def test_dockerfile_allows(tmp_path, body):
    assert await dockerfile_scanner(tmp_path, body).scan(tmp_path) == []


@pytest.mark.parametrize(
    ("body", "rule"),
    [
        ("FROM python:3.12\n", "from-not-allowed"),
        (f"FROM python@{OTHER}\n", "from-not-allowed"),
        ("ARG BASE=x\nFROM ${BASE}\n", "from-not-allowed"),
        (f"# syntax=docker/dockerfile:1\nFROM python@{DIGEST}\n", "syntax-directive"),
        (f"FROM python@{DIGEST}\nCOPY --from=evil/image:1 /a /b\n", "from-not-allowed"),
    ],
)
async def test_dockerfile_rejects(tmp_path, body, rule):
    findings = await dockerfile_scanner(tmp_path, body).scan(tmp_path)
    assert [f.rule_id for f in findings] == [rule]
    assert findings[0].reason_code == "base_not_allowed"
    assert findings[0].file == "Dockerfile"
    assert findings[0].line >= 1
    with pytest.raises(DeployRejectedError) as exc:
        await run_prescan(tmp_path, [DockerfileScanner(frozenset({DIGEST}))])
    assert exc.value.reason_code == "base_not_allowed"


async def test_dockerfile_missing(tmp_path):
    findings = await DockerfileScanner(frozenset()).scan(tmp_path)
    assert findings == [Finding("dockerfile-missing", "Dockerfile", 0, "dockerfile_missing")]

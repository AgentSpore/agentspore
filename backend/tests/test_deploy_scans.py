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

import shutil
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
    run = fake_run(42, (FIXTURES / "gitleaks_report.json").read_bytes())
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


async def test_gitleaks_runs_on_platform_config_not_the_repos(tmp_path):
    seen = {}

    async def run(argv, *, cwd, timeout, env=None):
        flags = dict(zip(argv, argv[1:], strict=False))
        seen["argv"] = argv
        seen["config"] = Path(flags["--config"]).read_text()
        seen["ignore"] = Path(flags["--gitleaks-ignore-path"]).read_text()
        return 0, b"[]"

    await GitleaksScanner(run).scan(tmp_path)
    assert "--ignore-gitleaks-allow" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--exit-code") + 1] == "42"
    assert seen["config"] == "[extend]\nuseDefault = true\n"
    assert seen["ignore"] == ""


@pytest.mark.parametrize(
    ("rc", "stdout"),
    [
        (1, b""),  # gitleaks fatal errors exit 1 with no report: must not read as "clean"
        (1, b"[]"),
        (2, b"[]"),
        (42, b"[]"),
        (42, b""),
        (42, b"not json"),
        (0, b""),
        (0, b"{}"),
        (42, b'[{"File": "a.py"}]'),
        (42, b'["not a dict"]'),
    ],
)
async def test_gitleaks_unusable_report_is_tool_failure(fake_run, tmp_path, rc, stdout):
    with pytest.raises(DeployRejectedError) as exc:
        await GitleaksScanner(fake_run(rc, stdout)).scan(tmp_path)
    assert exc.value.reason_code == "tool_failed"


async def test_gitleaks_clean(fake_run, tmp_path):
    assert await GitleaksScanner(fake_run(0, b"[]")).scan(tmp_path) == []


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks binary not installed")
async def test_real_gitleaks_ignores_repo_controlled_allowlists(tmp_path):
    token = "ghp_" + "aB3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6"
    (tmp_path / "app.py").write_text(f'x = 1\ntoken = "{token}"\n')
    (tmp_path / ".gitleaks.toml").write_text('[allowlist]\npaths = [".*"]\n')
    (tmp_path / "allowed.py").write_text(f'y = "{token}"  # gitleaks:allow\n')
    findings = await GitleaksScanner().scan(tmp_path)
    assert sorted((f.rule_id, f.file, f.line) for f in findings) == [
        ("github-pat", "allowed.py", 1),
        ("github-pat", "app.py", 2),
    ]


async def test_gitleaksignore_in_the_tree_is_refused_without_running_the_tool(fake_run, tmp_path):
    (tmp_path / ".gitleaksignore").write_text("app.py:github-pat:2\n")
    run = fake_run(0, b"[]")
    findings = await GitleaksScanner(run).scan(tmp_path)
    assert [(f.rule_id, f.reason_code, f.blocking) for f in findings] == [
        ("gitleaksignore-present", "source_unsafe", True)
    ]
    assert run.calls == []


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


async def test_osv_runs_on_empty_platform_config(tmp_path):
    seen = {}

    async def run(argv, *, cwd, timeout, env=None):
        config = next(a for a in argv if a.startswith("--config="))
        seen["config"] = Path(config.removeprefix("--config=")).read_text()
        return 0, b""

    await OsvScanner(run).scan(tmp_path)
    assert seen["config"] == ""


@pytest.mark.parametrize(
    "stdout",
    [
        b"",
        b"[]",
        b"not json",
        b"{}",
        b'{"results": []}',
        b'{"results": [{"x": 1}]}',
        b'{"results": ["a"]}',
    ],
)
async def test_osv_vulnerable_exit_with_unusable_report_is_tool_failure(fake_run, tmp_path, stdout):
    with pytest.raises(DeployRejectedError) as exc:
        await OsvScanner(fake_run(1, stdout)).scan(tmp_path)
    assert exc.value.reason_code == "tool_failed"


async def test_osv_critical_fails_and_exit_codes(fake_run, tmp_path):
    with pytest.raises(DeployRejectedError) as exc:
        await run_prescan(
            tmp_path, [OsvScanner(fake_run(1, (FIXTURES / "osv_report.json").read_bytes()))]
        )
    assert exc.value.reason_code == "dependency_vulnerable"
    assert await OsvScanner(fake_run(128)).scan(tmp_path) == []
    assert await OsvScanner(fake_run(0)).scan(tmp_path) == []
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
        f"FROM python@{DIGEST} AS a\nFROM python@{DIGEST}\nCOPY --from=0 /a /b\n",
        f"FROM python@{DIGEST} AS a\nRUN --mount=type=bind,from=a,target=/x true\n",
        f"FROM python@{DIGEST}\nRUN --mount=type=bind,from=img@{DIGEST},target=/x true\n",
        f"FROM python@{DIGEST}\n# escape is only a word here\n",
        f"FROM python@{DIGEST}\nRUN --mount=type=cache,target=/root/.cache,sharing=locked true\n",
        f"FROM python@{DIGEST}\nRUN --mount=type=bind,source=.,target=/x,ro true\n",
        f"FROM python@{DIGEST}\nRUN --mount=type=bind,target=/x \\\n  true\n",
        f"FROM --platform=$BUILDPLATFORM python@{DIGEST}\n",
        f"FROM python@{DIGEST}\r\nRUN --mount=type=bind,target=/x \\\r\n  true\r\n",
        f"FROM --platform=${{TARGETPLATFORM}} python@{DIGEST}\n",
        f"FROM python@{DIGEST}\nRUN --mount=type=secret,id=x,env=TOKEN true\n",
        f"FROM python@{DIGEST}\n# комментарий \u2014 not code\nCMD x\n",
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
        (
            f"FROM python@{DIGEST}\nRUN --mount=type=bind,from=evil/image:1,target=/x true\n",
            "from-not-allowed",
        ),
        (
            f"FROM python@{DIGEST}\nRUN --mount=type=cache,target=/c,From=evil true\n",
            "dockerfile-unsupported",
        ),
        (
            f"FROM python@{DIGEST}\nRUN --mount=type=bind,from=0,target=/x true\n",
            "from-not-allowed",
        ),
        ("FROM 0\n", "from-not-allowed"),
        (f"# escape=`\nFROM python@{DIGEST}\nRUN echo \\\nFROM evil\n", "parser-directive"),
        (f"# ESCAPE = `\nFROM python@{DIGEST}\n", "parser-directive"),
        (f"# check=skip=all\nFROM python@{DIGEST}\n", "parser-directive"),
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


UNSUPPORTED = "dockerfile-unsupported"
BS = "\\"  # one backslash


@pytest.mark.parametrize(
    ("instruction", "rule"),
    [
        ('RUN --mount=type=bind,"from=evil/img:latest",target=/x true', UNSUPPORTED),
        ('RUN --mount="from=evil/img:latest,target=/x" true', UNSUPPORTED),
        ('RUN --mount=type=bind,"FROM = evil/img:1",target=/x true', UNSUPPORTED),
        ('RUN --mount=type=bind,"from=evil/img:latest', UNSUPPORTED),
        ("RUN --mount=type=bind,'from=evil/img:latest',target=/x true", UNSUPPORTED),
        ('RUN --mount=type=bind,fr"om"=evil/img:latest,target=/x true', UNSUPPORTED),
        (f"RUN --mount=type=bind,f{BS}rom=evil/img:latest,target=/x true", UNSUPPORTED),
        (f"RUN --mount=type=bind,{BS}\nfrom=evil/img:latest,target=/x true", "from-not-allowed"),
        (f"RUN --mount=type=bind,{BS}\n# note\nfrom=evil/img:latest,target=/x", "from-not-allowed"),
        (f"RUN --mount=type=bind,{BS}\n\nfrom=evil/img:latest,target=/x true", "from-not-allowed"),
        ("RUN --mount=type=bind,From=evil/img:latest,target=/x true", UNSUPPORTED),
        ("RUN --mount=type=bind,bogus=1,target=/x true", UNSUPPORTED),
        ("RUN --mount=type=weird,target=/x true", UNSUPPORTED),
        ("RUN --mount=type=bind,target=/x,,from=a true", UNSUPPORTED),
        ("RUN --mount=type=bind,target=${T} true", UNSUPPORTED),
        ("COPY --from='evil/img:1' /a /b", UNSUPPORTED),
        ('COPY --fr"om"=evil/img:latest /a /b', UNSUPPORTED),
        ('RUN --mo"unt"=type=bind,from=evil/img:latest,target=/x true', UNSUPPORTED),
        ("COPY --chown=${UID} x /x", UNSUPPORTED),
        ("COPY --Chown=1 x /x", UNSUPPORTED),
        ("ONBUILD COPY --from=evil/img:latest /a /b", UNSUPPORTED),
        ("ONBUILD RUN --mount=type=bind,from=evil/img:latest,target=/x true", UNSUPPORTED),
        ("RUN echo é", UNSUPPORTED),
        (
            f"RUN --network=none{BS}\n    --mount=type=bind,from=evil/img:latest,target=/x true",
            "from-not-allowed",
        ),
    ],
)
async def test_dockerfile_flags_outside_the_strict_grammar_are_refused(tmp_path, instruction, rule):
    body = f"FROM python@{DIGEST}\n{instruction}\n"
    findings = await dockerfile_scanner(tmp_path, body).scan(tmp_path)
    assert [f.rule_id for f in findings] == [rule]


async def test_dockerfile_missing(tmp_path):
    findings = await DockerfileScanner(frozenset()).scan(tmp_path)
    assert findings == [Finding("dockerfile-missing", "Dockerfile", 0, "dockerfile_missing")]


@pytest.mark.parametrize(
    "char", ["\r", "\f", "\v", "\x1c", "\x1e", "\x85", "\u2028", "\u2029", "\x00"]
)
async def test_dockerfile_with_exotic_whitespace_is_refused(tmp_path, char):
    body = (
        f"FROM python@{DIGEST}\nRUN {char}--mount=type=bind,from=evil/img:latest,target=/x true\n"
    )
    (tmp_path / "Dockerfile").write_bytes(body.encode())
    findings = await DockerfileScanner(frozenset({DIGEST})).scan(tmp_path)
    assert [f.rule_id for f in findings] == [UNSUPPORTED]
    assert findings[0].line == 2


@pytest.mark.parametrize(
    ("raw", "rule"),
    [
        (b"\xef\xbb\xbfFROM evil/img:latest\n", "from-not-allowed"),
        (
            b"FROM python@"
            + DIGEST.encode()
            + b"\nRUN \x85--mount=type=bind,from=evil/i,target=/x\n",
            UNSUPPORTED,
        ),
        (
            b"FROM python@"
            + DIGEST.encode()
            + b"\nRUN \xa0--mount=type=bind,from=evil/i,target=/x\n",
            UNSUPPORTED,
        ),
        (
            b"FROM python@"
            + DIGEST.encode()
            + b"\nRUN \xc2\xa0--mount=type=bind,from=evil/i,target=/x\n",
            UNSUPPORTED,
        ),
    ],
)
async def test_dockerfile_bytes_follow_buildkit_or_are_refused(tmp_path, raw, rule):
    (tmp_path / "Dockerfile").write_bytes(raw)
    findings = await DockerfileScanner(frozenset({DIGEST})).scan(tmp_path)
    assert [f.rule_id for f in findings] == [rule]


async def test_dockerfile_bom_is_dropped_before_parsing(tmp_path):
    (tmp_path / "Dockerfile").write_bytes(b"\xef\xbb\xbfFROM python@" + DIGEST.encode() + b"\n")
    assert await DockerfileScanner(frozenset({DIGEST})).scan(tmp_path) == []

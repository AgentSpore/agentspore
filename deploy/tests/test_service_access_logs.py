"""Exercise the pinned native Caddy encoder using only synthetic requests."""

import http.client
import json
import subprocess
import time
import uuid
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
IMAGE = "agentspore-caddy:oss-local-preflight-20261020"
HOSTS = [
    "agentspore.com",
    "agentspore.ru",
    "www.agentspore.com",
    "www.agentspore.ru",
    "api.agentspore.com",
    "api.agentspore.ru",
    "podmemory.agentspore.com",
    "podmemory.agentspore.ru",
    "otkrytka.agentspore.com",
    "otkrytka.agentspore.ru",
    "pereklichka.agentspore.com",
    "pereklichka.agentspore.ru",
]
FIELDS = {"ts", "service", "method", "status", "duration", "size", "route"}
MARKER = "SYNTHETIC_PRIVATE_MARKER"


def docker(*args: str, source: str | None = None) -> str:
    """Run fixed local Docker fixture commands with bounded output capture."""
    result = subprocess.run(
        ["docker", *args],
        input=source,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, "synthetic Docker operation failed"
    return result.stdout


def logging_snippet() -> str:
    """Reuse the production snippet with only its synthetic output path changed."""
    source = (DEPLOY / "Caddyfile").read_text()
    start = source.index("(service_access_log) {")
    end = source.index("\n# End service access logging", start)
    return source[start:end].replace(
        "/var/log/caddy/services.jsonl", "/fixture/services.jsonl"
    )


def test_native_full_config_host_coverage() -> None:
    """Adapt all actual public site definitions with a synthetic DNS token."""
    source = (
        (DEPLOY / "Caddyfile")
        .read_text()
        .replace(
            "import /etc/caddy/restored-services/*.caddy",
            (DEPLOY / "restored-services.caddy").read_text(),
        )
        .replace("{env.CLOUDFLARE_API_TOKEN}", "SYNTHETIC")
    )
    config = json.loads(
        docker(
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--cpus",
            ".5",
            "--memory",
            "128m",
            IMAGE,
            "caddy",
            "adapt",
            "--config",
            "/dev/stdin",
            "--adapter",
            "caddyfile",
            source=source,
        )
    )
    mappings = {}
    for server in config["apps"]["http"]["servers"].values():
        mappings.update(server.get("logs", {}).get("logger_names", {}))
    for host in HOSTS:
        assert len(mappings[host]) == 1
    for logger in config["logging"]["logs"].values():
        if logger.get("writer", {}).get("output") == "file":
            writer = logger["writer"]
            assert writer["filename"] == "/var/log/caddy/services.jsonl"
            assert writer["mode"] == "0600"
            assert writer["roll_size_mb"] == 25
            assert writer["roll_keep"] == 4
            assert writer["roll_keep_days"] == 7


def wait_ready(port: int, expected_status: int = 200) -> None:
    """Require a completed HTTP exchange across the real Docker port forward."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
        try:
            connection.request("HEAD", "/ready", headers={"Host": HOSTS[0]})
            response = connection.getresponse()
            assert response.status == expected_status
            response.read()
            return
        except (OSError, http.client.HTTPException):
            time.sleep(0.1)
        finally:
            connection.close()
    raise TimeoutError


def redirect_configuration() -> str:
    """Keep automatic HTTPS enabled, without acquiring any certificates."""
    config = "{\n admin off\n auto_https disable_certs\n http_port 8080\n https_port 8443\n}\n"
    config += logging_snippet()
    for host in HOSTS:
        config += f"\nhttps://{host}:8443 {{\n import service_access_log {host}\n respond 200\n}}\n"
    source = (DEPLOY / "Caddyfile").read_text()
    if "# Begin explicit HTTP redirects" in source:
        config += source.split("# Begin explicit HTTP redirects", 1)[1].split(
            "# End explicit HTTP redirects", 1
        )[0]
    return config


@pytest.fixture
def native_logger(tmp_path: Path, request: pytest.FixtureRequest):
    """Own one bounded native Caddy process and remove it after the fixture."""
    name = "service-log-test-" + uuid.uuid4().hex
    config = "{\n admin off\n auto_https off\n}\n" + logging_snippet()
    for host in HOSTS:
        config += f"\nhttp://{host}:8080 {{\n import service_access_log {host}\n"
        config += " header X-Synthetic SYNTHETIC_PRIVATE_MARKER\n"
        config += " handle /failure/* {\n reverse_proxy 127.0.0.1:1\n }\n"
        config += " handle /reject/* {\n respond 403\n }\n respond 200\n}\n"
    redirect = getattr(request, "param", "normal") == "redirect"
    if redirect:
        config = redirect_configuration()
    (tmp_path / "Caddyfile").write_text(config)
    docker(
        "run",
        "-d",
        "--name",
        name,
        "--cpus",
        ".5",
        "--memory",
        "128m",
        "--pids-limit",
        "64",
        "--log-driver",
        "none",
        "-p",
        "127.0.0.1::8080",
        "-v",
        f"{tmp_path}:/fixture",
        IMAGE,
        "caddy",
        "run",
        "--config",
        "/fixture/Caddyfile",
        "--adapter",
        "caddyfile",
    )
    try:
        port = int(docker("port", name, "8080/tcp").strip().rsplit(":", 1)[1])
        wait_ready(port, 308 if redirect else 200)
        yield port, tmp_path / "services.jsonl"
    finally:
        docker("rm", "-f", name)


def test_native_privacy_success_failure_and_unique_records(native_logger) -> None:
    """Check the access sink against sensitive synthetic request metadata."""
    port, output = native_logger
    expected = [(HOSTS[0], 200)]
    for host in HOSTS:
        for path, status in [("ok", 200), ("reject", 403), ("failure", 502)]:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request(
                "POST",
                f"/{path}/{MARKER}?token={MARKER}",
                MARKER,
                {
                    "Host": host,
                    "Authorization": MARKER,
                    "Cookie": MARKER,
                    "X-Forwarded-For": "192.0.2.99",
                    "User-Agent": MARKER,
                },
            )
            response = connection.getresponse()
            assert response.status == status
            response.read()
            connection.close()
            expected.append((host, status))
    for _ in range(30):
        lines = output.read_text().splitlines()
        if len(lines) == len(expected):
            break
        time.sleep(0.1)
    assert len(lines) == len(expected)
    rows = [json.loads(line) for line in lines]
    assert sorted((row["service"], row["status"]) for row in rows) == sorted(expected)
    assert output.stat().st_mode & 0o777 == 0o600
    assert [row["method"] for row in rows] == ["HEAD"] + ["POST"] * 36
    for row in rows:
        assert set(row) == FIELDS
        assert row["route"] == "other"
        assert isinstance(row["ts"], float)
        assert row["duration"] >= 0 and row["size"] >= 0
    assert MARKER not in "\n".join(lines)
    assert "192.0.2.99" not in "\n".join(lines)


def test_only_allowlisted_routes_are_logged(native_logger) -> None:
    """Personal paths and query strings collapse to an opaque constant."""
    port, output = native_logger
    paths = [
        ("/", "home"),
        ("/showcase?token=" + MARKER, "showcase"),
        ("/api/v1/agents/register?key=" + MARKER, "agent_registration"),
        ("/api/v1/auth/register", "user_registration"),
        ("/agents/" + MARKER, "other"),
    ]
    for path, _ in paths:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("GET", path, headers={"Host": HOSTS[0]})
        response = connection.getresponse()
        response.read()
        connection.close()
    for _ in range(30):
        lines = output.read_text().splitlines()
        if len(lines) == len(paths) + 1:
            break
        time.sleep(0.1)
    rows = [json.loads(line) for line in lines]
    assert [row["route"] for row in rows] == ["other"] + [label for _, label in paths]
    assert MARKER not in "\n".join(lines)


def test_persistent_mount_and_runbook() -> None:
    """Keep the persistence and operational contract connected to the config."""
    compose = (DEPLOY / "docker-compose.prod.yml").read_text()
    assert "- caddy_logs:/var/log/caddy" in compose
    assert "\n  caddy_logs:\n" in compose
    runbook = (DEPLOY.parent / "docs/ACCESS_LOGS.md").read_text()
    for contract in ["2.11.2", "0600", "25 MiB", "168h", "active file", "archive"]:
        assert contract in runbook


@pytest.mark.parametrize("native_logger", ["redirect"], indirect=True)
def test_native_automatic_https_redirect_privacy(native_logger) -> None:
    """Assert explicit HTTP logging closes the automatic HTTPS appender gap."""
    port, output = native_logger
    for host in HOSTS:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request(
            "POST",
            f"/{MARKER}?token={MARKER}",
            MARKER,
            {"Host": host, "Authorization": MARKER},
        )
        response = connection.getresponse()
        assert response.status == 308
        response.read()
        connection.close()
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == len(HOSTS) + 1
    for row in rows:
        assert set(row) == FIELDS
        assert row["service"] == "http_redirect"
        assert row["status"] == 308
    assert [row["method"] for row in rows] == ["HEAD"] + ["POST"] * len(HOSTS)
    assert MARKER not in output.read_text()

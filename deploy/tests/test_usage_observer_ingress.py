"""Exercise the tracked Otkrytka producer with native Caddy and synthetic HTTP peers."""

import ast
import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TypedDict, cast

DEPLOY = Path(__file__).resolve().parent.parent
KEY = "SYNTHETIC_OBSERVER_KEY"
VISITOR = "00000000-0000-4000-8000-000000000001"


class PeerState(TypedDict):
    seen: list[tuple[str, dict[str, str], str]]
    app: list[dict]
    mode: str


STATE: PeerState = {"seen": [], "app": [], "mode": "ok"}


def test_source_contract():
    source = (DEPLOY / "restored-services.caddy").read_text()
    assert "intercept {" in source
    assert "{env.OTKRYTKA_USAGE_OBSERVER_KEY}" in source
    assert "uuid delete" in (DEPLOY / "Caddyfile").read_text()
    policy = ast.parse((DEPLOY.parent / "backend/app/services/usage_service.py").read_text())
    probes = next(
        node
        for node in ast.walk(policy)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_PROBES" for target in node.targets)
    )
    assert isinstance(probes.value, ast.Call)
    expected = ast.literal_eval(probes.value.args[0])
    actual = source.split("(?i)^/(", 1)[1].split(")(/|$)", 1)[0].replace("[.]", ".").split("|")
    assert {"/" + path for path in actual} == expected


def request(port, method="GET", path="/", body=None, extra_headers=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    headers = {
        "Authorization": "Bearer SYNTHETIC",
        "User-Agent": "Mozilla/5.0 fixture Chrome/130.0",
        "Accept": "text/html",
        "Cookie": f"__Host-visitor={VISITOR}; private=PRIVATE",
        "X-Organizer-Token": "PRIVATE",
        "X-Usage-Kind": "FORGED",
    }
    headers.update(extra_headers or {})
    connection.request(method, path, body, headers)
    response = connection.getresponse()
    headers = dict(response.getheaders())
    headers["Set-Cookie"] = "; ".join(response.headers.get_all("Set-Cookie", []))
    result = response.status, headers, response.read()
    connection.close()
    return result


class Peer(BaseHTTPRequestHandler):
    def log_message(self, format: str, *_args: object) -> None:
        pass

    def do_GET(self):
        self.serve()

    def do_POST(self):
        self.serve()

    def serve(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if cast(ThreadingHTTPServer, self.server).server_port == 8001:
            STATE["seen"].append((self.path, dict(self.headers), body.decode()))
            if STATE["mode"] == "retry" and len(STATE["seen"]) == 1:
                self.close_connection = True
                return
            self.send_response(500 if STATE["mode"] == "fail" else 204)
            if STATE["mode"] != "empty":
                self.send_header("Set-Cookie", "__Host-visitor=synthetic; Secure; Path=/")
            self.end_headers()
            return
        if self.headers.get("Upgrade") == "websocket":
            self.upgrade_socket()
            return
        if self.path == "/state":
            payload = json.dumps(STATE).encode()
        elif self.path.startswith("/mode/"):
            STATE["mode"] = self.path.split("/")[-1]
            STATE["seen"].clear()
            if STATE["mode"] == "refused":
                OBSERVER.shutdown()
                OBSERVER.server_close()
            payload = b"ok"
        else:
            STATE["app"].append(
                {"headers": dict(self.headers), "size": len(body), "digest": hashlib.sha256(body).hexdigest()}
            )
            payload = b"<html>original</html>" if self.command == "GET" else b'{"saved":true}'
        status = 400 if self.path.startswith("/error") else 200
        self.send_response(status)
        self.send_header("Content-Type", "text/html" if self.command == "GET" else "application/json")
        self.send_header("X-App", "original")
        self.send_header("Set-Cookie", "app=synthetic; HttpOnly")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def upgrade_socket(self):
        assert self.path == "/api/v1/boards/synthetic/ws"
        assert self.headers["X-Organizer-Token"] == "PRIVATE" and "Authorization" not in self.headers
        key = self.headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", base64.b64encode(hashlib.sha1(key.encode()).digest()).decode())
        self.end_headers()
        assert self.rfile.read(8) == b"\x81\x82abcd\x0e\x09"
        self.wfile.write(b"\x81\x02ok")
        self.wfile.flush()


def checks():
    for _ in range(80):
        try:
            request(8080, "POST", "/ready")
            break
        except OSError:
            time.sleep(0.05)
    enabled = os.environ.get("ENABLED") == "1"
    for method, path, expected in [
        ("GET", "/private?organizer=PRIVATE", "visit"),
        ("POST", "/api/v1/boards?private=PRIVATE", "board_created"),
        ("POST", "/other", None),
        ("GET", "/error", None),
    ]:
        request(8000, path="/mode/ok")
        status, headers, body = request(8080, method, path, b"PRIVATE" if method == "POST" else None)
        assert status == (400 if path == "/error" else 200)
        assert headers["X-App"] == "original" and "app=synthetic" in headers["Set-Cookie"]
        assert body == (b"<html>original</html>" if method == "GET" else b'{"saved":true}')
        state = json.loads(request(8000, path="/state")[2])
        seen = state["seen"]
        assert len(seen) == int(enabled and expected is not None)
        if seen:
            endpoint, metadata, received_body = seen[0]
            assert endpoint == "/api/v1/_internal/usage/otkrytka/observe" and not received_body
            assert metadata["X-Usage-Kind"] == expected
            assert "__Host-visitor=" in headers["Set-Cookie"]
            assert metadata["X-Usage-Visitor-Id"] == VISITOR
            assert uuid.UUID(metadata["X-Usage-Request-Id"]).version == 4
            assert metadata["Authorization"] == "Bearer SYNTHETIC"
            assert metadata["X-Usage-Observer-Key"] == KEY
            assert "PRIVATE" not in json.dumps(seen)
            assert not {"Cookie", "X-Organizer-Token", "X-Forwarded-For"} & metadata.keys()
        app = state["app"][-1]
        assert "Authorization" not in app["headers"]
        assert app["headers"]["X-Organizer-Token"] == "PRIVATE"
    probe_checks(enabled)
    application_transport_checks()
    if enabled:
        failure_checks()
    rows = [json.loads(line) for line in Path("/fixture/services.jsonl").read_text().splitlines()]
    assert rows and all(set(row) == {"ts", "service", "method", "status", "duration", "size", "route"} for row in rows)
    print("native ingress checks passed")


def probe_checks(enabled):
    """Mirror UsageService._PROBES exact paths and descendants, including case folding."""
    probes = ["health", "healthz", "ready", "readyz", "readiness", "live", "livez", "liveness"]
    probes += ["metrics", "robots.txt", "favicon.ico", "api", "static", "assets"]
    cases = [("/" + probe + suffix, False) for probe in probes for suffix in ["", "/synthetic"]]
    for path, visit in cases + [("/READY/synthetic", False), ("/ready-made", True)]:
        request(8000, path="/mode/ok")
        status, headers, body = request(8080, path=path)
        assert status == 200 and body == b"<html>original</html>"
        assert headers["X-App"] == "original" and "app=synthetic" in headers["Set-Cookie"]
        seen = json.loads(request(8000, path="/state")[2])["seen"]
        assert len(seen) == int(enabled and visit), path
        assert all(record[1]["X-Usage-Path-Class"] == "document" for record in seen)


def application_transport_checks():
    """Mirror pinned boards.py upload and WebSocket paths; peers test transport only."""
    request(8000, path="/mode/ok")
    payload = (
        b'--fixture\r\nContent-Disposition: form-data; name="file"; filename="synthetic.png"\r\n'
        b"Content-Type: image/png\r\n\r\n" + b"x" * (3 * 1024 * 1024) + b"\r\n--fixture--\r\n"
    )
    status, headers, body = request(
        8080,
        "POST",
        "/api/v1/boards/synthetic/upload",
        payload,
        {"Content-Type": "multipart/form-data; boundary=fixture"},
    )
    assert status == 200 and body == b'{"saved":true}' and headers["X-App"] == "original"
    state = json.loads(request(8000, path="/state")[2])
    assert not state["seen"] and state["app"][-1]["size"] == len(payload)
    assert state["app"][-1]["digest"] == hashlib.sha256(payload).hexdigest()
    assert state["app"][-1]["headers"]["X-Organizer-Token"] == "PRIVATE"
    connection = HTTPConnection("127.0.0.1", 8080, timeout=3)
    connection.request(
        "GET",
        "/api/v1/boards/synthetic/ws",
        headers={
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": "c3ludGhldGljZml4dHVyZQ==",
            "Sec-WebSocket-Version": "13",
            "X-Organizer-Token": "PRIVATE",
            "Authorization": "Bearer SYNTHETIC",
        },
    )
    response = connection.getresponse()
    assert response.status == 101 and response.headers["Upgrade"] == "websocket"
    assert response.headers["Sec-WebSocket-Accept"] == "OpUDZ0P5r1eX1PA2OAVXCL7E4OE="
    connection.send(b"\x81\x82abcd\x0e\x09")
    assert response.fp.read(4) == b"\x81\x02ok"
    connection.close()
    assert not json.loads(request(8000, path="/state")[2])["seen"]


def failure_checks():
    for mode in ["retry", "empty", "fail", "refused"]:
        request(8000, path="/mode/" + mode)
        time.sleep(1.1)
        started = time.monotonic()
        status, headers, body = request(8080)
        assert time.monotonic() - started < 1.2
        assert status == 200 and body == b"<html>original</html>"
        assert headers["X-App"] == "original"
        assert "app=synthetic" in headers["Set-Cookie"]
        if mode != "retry":
            assert "__Host-visitor=" not in headers["Set-Cookie"]
        else:
            assert "__Host-visitor=" not in headers["Set-Cookie"]
            state = json.loads(request(8000, path="/state")[2])
            ids = [record[1]["X-Usage-Request-Id"] for record in state["seen"]]
            assert len(ids) == 1 and uuid.UUID(ids[0]).version == 4


def docker(*args, source=None):
    result = subprocess.run(["docker", *args], input=source, text=True, capture_output=True, timeout=25, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def configuration():
    source = (DEPLOY / "restored-services.caddy").read_text().split("podmemory.")[0]
    source = source.replace("otkrytka.agentspore.com, otkrytka.agentspore.ru", ":8080")
    source = source.replace("restored-otkrytka:8000", "127.0.0.1:8000").replace("backend:8000", "127.0.0.1:8001")
    logging = (DEPLOY / "Caddyfile").read_text().split("# End service access logging")[0]
    logging = logging.replace("/var/log/caddy/services.jsonl", "/fixture/services.jsonl")
    return "{\n admin off\n auto_https off\n log {\n output discard\n }\n}\n" + logging + source


def create_peer(folder, owned):
    options = (
        ["--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
        + ["--memory", "96m", "--cpus", ".4", "--pids-limit", "32"]
        + ["--user", "501:20", "--log-driver", "none", "-v", f"{folder}:/fixture"]
    )
    peer = docker(
        "create",
        "--network",
        "none",
        *options,
        "--entrypoint",
        "python",
        "python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1",
        "/fixture/peer.py",
        "--serve",
    )
    owned.append(peer)
    docker("start", peer)
    return peer, options


def test_native_ingress(tmp_path):
    """No published ports: synthetic peers and Caddy share one owned namespace."""
    baseline = set(docker("ps", "-q", "--no-trunc").splitlines())
    owned = []
    timer = threading.Timer(
        180,
        lambda: [
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=10, check=False) for cid in owned
        ],
    )
    timer.start()
    try:
        for enabled in [True, False]:
            folder = tmp_path / str(enabled)
            folder.mkdir(mode=0o777)
            folder.chmod(0o777)
            (folder / "Caddyfile").write_text(configuration())
            (folder / "peer.py").write_text(Path(__file__).read_text())
            peer, options = create_peer(folder, owned)
            caddy = docker(
                "create",
                "--network",
                f"container:{peer}",
                *options,
                "--tmpfs",
                "/tmp:rw,exec,size=64m,mode=1777",
                *(["-e", "OTKRYTKA_USAGE_OBSERVER_KEY=" + KEY] if enabled else []),
                "--entrypoint",
                "/bin/sh",
                "caddy@sha256:834468128c7696cec0ceea6172f7d692daf645ae51983ca76e39da54a97c570d",
                "-c",
                "cp /usr/bin/caddy /tmp/caddy && test \"$(sha256sum /usr/bin/caddy | cut -d' ' -f1)\" = "
                "\"$(sha256sum /tmp/caddy | cut -d' ' -f1)\" && /tmp/caddy run --config /fixture/Caddyfile",
            )
            owned.append(caddy)
            docker("start", caddy)
            result = docker(
                "exec", "-e", "ENABLED=" + str(int(enabled)), peer, "python", "/fixture/peer.py", "--checks"
            )
            assert "native ingress checks passed" in result
            adapted = json.loads(docker("exec", caddy, "/tmp/caddy", "adapt", "--config", "/fixture/Caddyfile"))
            servers = adapted["apps"]["http"]["servers"].values()
            assert any(server["listen"] == ["127.0.0.1:8081"] for server in servers)
    finally:
        timer.cancel()
        for cid in reversed(owned):
            docker("rm", "-f", cid)
        assert baseline <= set(docker("ps", "-q", "--no-trunc").splitlines())


def test_compose_optional_environment(tmp_path):
    """Resolve only synthetic values, then prove null mappings omit runtime variables."""
    source = (DEPLOY / "docker-compose.prod.yml").read_text()
    assert source.count("      OTKRYTKA_USAGE_OBSERVER_KEY:\n") == 2
    assert source.count("      OTKRYTKA_USAGE_PROJECT_ID:\n") == 1
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  probe:\n    image: python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1\n    network_mode: none\n"
        "    cap_drop: [ALL]\n    security_opt: [no-new-privileges]\n"
        "    mem_limit: 64m\n    cpus: 0.3\n    pids_limit: 32\n    environment:\n"
        "      OTKRYTKA_USAGE_PROJECT_ID:\n      OTKRYTKA_USAGE_OBSERVER_KEY:\n"
    )
    clean = {name: os.environ[name] for name in ["PATH", "HOME"]}
    for configured in [False, True]:
        values = tmp_path / "synthetic-vars"
        values.write_text(
            f"OTKRYTKA_USAGE_PROJECT_ID={VISITOR}\nOTKRYTKA_USAGE_OBSERVER_KEY={KEY}\n" if configured else ""
        )
        command = [
            "docker",
            "compose",
            "--env-file",
            str(values),
            "-f",
            str(compose),
            "run",
            "--rm",
            "--no-deps",
            "probe",
            "python",
            "-c",
            "import os; print(sum(k.startswith('OTKRYTKA_USAGE_') for k in os.environ))",
        ]
        result = subprocess.run(command, env=clean, capture_output=True, text=True, timeout=25, check=False)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ("2" if configured else "0")


if __name__ == "__main__":
    if sys.argv[-1] == "--serve":
        OBSERVER = ThreadingHTTPServer(("127.0.0.1", 8001), Peer)
        threading.Thread(target=OBSERVER.serve_forever, daemon=True).start()
        ThreadingHTTPServer(("127.0.0.1", 8000), Peer).serve_forever()
    else:
        checks()

"""Strict growth proposals and bounded native static-prototype verification."""

import hashlib
import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from .operator_digest import canonical, unique_object

FILE_PATH = re.compile(
    r"(?:[A-Za-z0-9_-]+\.(?:html|css|js|json|md)|tests/[a-z0-9_-]+\.test\.js)\Z"
)
TITLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,69}\Z")
SERVICE_KEYS = {"title", "description", "source_ids", "files"}
NODE = "/opt/agentspore-codex/node/bin/node"
BWRAP = "/usr/bin/bwrap"


def _object(value, keys: set) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("growth_shape_invalid")


def _text(value, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("growth_text_invalid")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("growth_text_invalid") from exc


def _ids(values, known: dict | None, limit: int = 8) -> None:
    if not isinstance(values, list) or not 1 <= len(values) <= limit:
        raise ValueError("growth_evidence_invalid")
    if any(not isinstance(value, str) for value in values):
        raise ValueError("growth_evidence_invalid")
    if known is not None and any(value not in known for value in values):
        raise ValueError("growth_evidence_invalid")
    if len(set(values)) != len(values):
        raise ValueError("growth_evidence_invalid")


def _manifest(service: dict) -> list[str]:
    _object(service, SERVICE_KEYS)
    _text(service["title"], 70)
    if not TITLE.fullmatch(service["title"]):
        raise ValueError("growth_title_invalid")
    _text(service["description"], 1500)
    _ids(service["source_ids"], None)
    if len(service["source_ids"]) < 2:
        raise ValueError("growth_demand_missing")
    files = service["files"]
    if not isinstance(files, list) or not 2 <= len(files) <= 12:
        raise ValueError("growth_files_invalid")
    paths, size = [], 0
    for entry in files:
        _object(entry, {"path", "content"})
        path, content = entry["path"], entry["content"]
        if not isinstance(path, str) or not FILE_PATH.fullmatch(path):
            raise ValueError("growth_path_invalid")
        if path.casefold() == "package.json" or path.casefold() in {
            p.casefold() for p in paths
        }:
            raise ValueError("growth_path_invalid")
        _text(content, 40000)
        size += len(content.encode("utf-8"))
        paths.append(path)
    if size > 40000 or not {"index.html", "README.md"}.issubset(paths):
        raise ValueError("growth_files_invalid")
    tests = [path for path in paths if path.startswith("tests/")]
    if not tests:
        raise ValueError("growth_tests_missing")
    return tests


def _service_evidence(service: dict, bundle: dict) -> None:
    _manifest(service)
    coverage = bundle["coverage"]
    known = bundle["facts"]
    _ids(service["source_ids"], known)
    ids = service["source_ids"]
    kinds, urls = coverage.get("source_kinds", {}), coverage.get("source_urls", {})
    if len(ids) < 2 or any(kinds.get(key) != "demand" for key in ids):
        raise ValueError("growth_demand_missing")
    selected = [urls.get(key) for key in ids]
    if any(not isinstance(url, str) or not url for url in selected):
        raise ValueError("growth_demand_missing")
    if len(set(selected)) != len(selected):
        raise ValueError("growth_demand_duplicate")
    titles = coverage.get("project_titles", [])
    if any(service["title"].casefold() == title.casefold() for title in titles):
        raise ValueError("growth_project_duplicate")


def parse_growth(raw: str, bundle: dict) -> dict:
    """Validate model proposals against the collector's known public evidence."""
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
            raise ValueError("growth_result_too_large")
        result = json.loads(raw, object_pairs_hook=unique_object)
        canonical(result)
    except (UnicodeError, RecursionError, TypeError, ValueError) as exc:
        raise ValueError("growth_json_invalid") from exc
    _object(result, {"snapshot_digest", "ideas", "candidates", "service"})
    if result["snapshot_digest"] != bundle["snapshot_digest"]:
        raise ValueError("growth_digest_mismatch")
    facts, coverage = bundle["facts"], bundle["coverage"]
    _proposals(result["ideas"], facts)
    _candidates(result["candidates"], facts, coverage)
    if result["service"] is not None:
        _service_evidence(result["service"], bundle)
    creates = [idea for idea in result["ideas"] if idea["decision"] == "create"]
    if len(creates) > 1 or bool(creates) != (result["service"] is not None):
        raise ValueError("growth_create_mismatch")
    if creates and set(creates[0]["evidence_ids"]) != set(
        result["service"]["source_ids"]
    ):
        raise ValueError("growth_create_mismatch")
    return result


def _proposals(ideas, facts: dict) -> None:
    if not isinstance(ideas, list) or len(ideas) > 5:
        raise ValueError("growth_ideas_invalid")
    for idea in ideas:
        _object(idea, {"evidence_ids", "decision", "reason", "target_user"})
        _ids(idea["evidence_ids"], facts)
        if idea["decision"] not in ("extend", "create", "defer"):
            raise ValueError("growth_decision_invalid")
        _text(idea["reason"], 1000)
        _text(idea["target_user"], 300)


def _candidates(candidates, facts: dict, coverage: dict) -> None:
    if not isinstance(candidates, list) or len(candidates) > 5:
        raise ValueError("growth_candidates_invalid")
    for candidate in candidates:
        _object(candidate, {"evidence_id", "fit", "invitation_draft"})
        key = candidate["evidence_id"]
        _ids([key], facts)
        if coverage.get("source_kinds", {}).get(key) != "agent":
            raise ValueError("growth_agent_evidence_invalid")
        _text(candidate["fit"], 500)
        _text(candidate["invitation_draft"], 1000)


def service_digest(service: dict) -> str:
    """Bind a verification receipt to the exact structurally valid manifest."""
    _manifest(service)
    return hashlib.sha256(canonical(service)).hexdigest()


def validate_service(service: dict) -> dict:
    """Validate a static manifest independently before artifact publication."""
    _manifest(service)
    return service


def _terminate(process: subprocess.Popen) -> None:
    for sig, wait in ((signal.SIGTERM, 0.2), (signal.SIGKILL, 0.5)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            continue
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()
    if process.poll() is None:
        raise ValueError("growth_tests_cleanup_failed")


def _test_output(process: subprocess.Popen, deadline: float) -> bytes:
    output = bytearray()
    with selectors.DefaultSelector() as selector:
        for stream in (process.stdout, process.stderr):
            if stream is None:
                raise ValueError("growth_tests_pipe_missing")
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("growth_tests_timeout")
            for key, _ in selector.select(min(remaining, 0.1)):
                data = os.read(key.fd, 8192)
                if not data:
                    selector.unregister(key.fileobj)
                else:
                    output.extend(data)
                    if len(output) > 65536:
                        raise ValueError("growth_tests_output_limit")
    try:
        code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        raise ValueError("growth_tests_timeout") from exc
    if code != 0:
        raise ValueError("growth_tests_failed")
    return bytes(output)


def _tap_passes(output: bytes) -> int:
    totals = {}
    for name in (b"tests", b"pass", b"fail"):
        matches = re.findall(rb"^# " + name + rb" ([0-9]+)\r?$", output, re.MULTILINE)
        if len(matches) != 1:
            raise ValueError("growth_tests_receipt_invalid")
        totals[name] = int(matches[0])
    if not 1 <= totals[b"pass"] <= totals[b"tests"] or totals[b"fail"]:
        raise ValueError("growth_tests_receipt_invalid")
    return totals[b"pass"]


def _prototype_argv(directory: str, tests: list[str]) -> list[str]:
    if not os.path.isfile(BWRAP) or not os.access(BWRAP, os.X_OK):
        raise ValueError("growth_test_isolation_unavailable")
    args = [
        BWRAP,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        str(Path(NODE).parent.parent),
        "/node",
        "--ro-bind",
        "/lib",
        "/lib",
    ]
    if os.path.isdir("/lib64"):
        args.extend(["--ro-bind", "/lib64", "/lib64"])
    return args + [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        directory,
        "/work",
        "--chdir",
        "/work",
        "--clearenv",
        "--setenv",
        "PATH",
        "/node/bin",
        "--setenv",
        "HOME",
        "/tmp",
        "/node/bin/node",
        "--test",
        "--test-reporter=tap",
        *tests,
    ]


def _verify_bytes(root: Path, service: dict) -> None:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    root_fd = os.open(root, flags | os.O_DIRECTORY)
    try:
        for entry in service["files"]:
            parts = entry["path"].split("/")
            parent_fd = (
                os.open("tests", flags | os.O_DIRECTORY, dir_fd=root_fd)
                if len(parts) == 2
                else os.dup(root_fd)
            )
            try:
                fd = os.open(parts[-1], flags, dir_fd=parent_fd)
                with os.fdopen(fd, "rb") as stream:
                    expected = entry["content"].encode("utf-8")
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size != len(expected):
                        raise ValueError("growth_files_changed")
                    if stream.read(len(expected) + 1) != expected:
                        raise ValueError("growth_files_changed")
            finally:
                os.close(parent_fd)
    except OSError as exc:
        raise ValueError("growth_files_changed") from exc
    finally:
        os.close(root_fd)


def verify_prototype(service: dict, deadline: float) -> dict:
    """Run bounded Node tests in a mandatory isolated filesystem and network."""
    tests = _manifest(service)
    if not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise ValueError("growth_deadline_invalid")
    deadline = min(deadline, time.monotonic() + 15)
    if deadline <= time.monotonic():
        raise ValueError("growth_tests_timeout")
    node = NODE
    if not os.path.isfile(node) or not os.access(node, os.X_OK):
        raise ValueError("growth_node_unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="agentspore-prototype-") as directory:
            root = Path(directory)
            (root / "tests").mkdir(mode=0o700)
            for entry in service["files"]:
                fd = os.open(
                    root / entry["path"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(fd, "wb") as stream:
                    stream.write(entry["content"].encode("utf-8"))
            process = subprocess.Popen(
                _prototype_argv(directory, tests),
                cwd=root,
                env={
                    "PATH": str(Path(node).parent),
                    "LANG": "C.UTF-8",
                    "HOME": directory,
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                passed = _tap_passes(_test_output(process, deadline))
            finally:
                _terminate(process)
            _verify_bytes(root, service)
    except OSError as exc:
        raise ValueError("growth_tests_execution_failed") from exc
    return {"service_digest": service_digest(service), "tests_passed": passed}

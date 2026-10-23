"""Synthetic CLI subprocess and accepted-socket boundary tests; no model calls."""

import json
import os
import re
import struct
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from agentspore_sdk import operator_analyst as analyst

DIGEST = "a" * 64


@pytest.fixture(autouse=True)
def clean_synthetic_child_environment(monkeypatch):
    for name in tuple(os.environ):
        if re.search(
            r"API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH_SOCK", name.upper()
        ):
            monkeypatch.delenv(name)


@pytest.fixture
def bundle():
    return {
        "snapshot_digest": DIGEST,
        "window": {
            "since": "2026-10-20T00:00:00+00:00",
            "until": "2026-10-21T00:00:00+00:00",
        },
        "facts": {"health.platform": "Platform HTTP status: 200."},
        "coverage": {"counters": {"status": "complete", "reasons": []}},
    }


@pytest.fixture
def events():
    analysis = {
        "snapshot_digest": DIGEST,
        "highlights": ["health.platform"],
        "attention": [],
        "recommendations": [],
    }
    return [
        {"type": "thread.started", "thread_id": "synthetic-thread"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "id": "item_0",
                "type": "reasoning",
                "text": "Public evidence only.",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "agent_message",
                "text": json.dumps(analysis),
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 2,
                "cache_write_input_tokens": 0,
                "output_tokens": 4,
                "reasoning_output_tokens": 1,
            },
        },
    ]


@pytest.fixture
def executable(tmp_path, monkeypatch):
    def install(body):
        path = tmp_path / "synthetic-cli"
        path.write_text(
            "#!" + sys.executable + "\nimport json, os, signal, sys\n" + body
        )
        path.chmod(0o700)
        monkeypatch.setattr(analyst, "ARGV", (str(path), *analyst.ARGV[1:]))
        monkeypatch.setattr(analyst, "WORK", str(tmp_path))
        return tmp_path

    return install


def test_real_subprocess_fixed_argv_prompt_and_usage(executable, bundle, events):
    directory = executable(
        "data=sys.stdin.read()\n"
        "open('capture.json','w').write(json.dumps({'argv':sys.argv[1:],'stdin':data}))\n"
        + "\n".join("print(" + repr(json.dumps(item)) + ")" for item in events)
    )
    result = analyst.run_analysis(bundle)
    assert json.loads(result["analysis"])["snapshot_digest"] == DIGEST
    assert result["usage"]["input_tokens"] == 10
    capture = json.loads((directory / "capture.json").read_text())
    assert capture["argv"] == list(analyst.ARGV[1:])
    assert (
        "--ignore-user-config" in capture["argv"] and "--ephemeral" in capture["argv"]
    )
    assert 'model="gpt-6.1-sol"' in capture["argv"]
    assert (
        'model_catalog_json="/opt/agentspore-codex/operator-workspace/models.json"'
        in capture["argv"]
    )
    assert capture["argv"].count("--dangerously-bypass-approvals-and-sandbox") == 1
    assert all(
        not option.startswith(("permissions.", "default_permissions="))
        for option in capture["argv"]
    )
    assert "--sandbox" not in capture["argv"]
    assert "--ask-for-approval" not in capture["argv"]
    assert "code_mode_host" not in capture["argv"]
    assert capture["argv"][capture["argv"].index("code_mode") - 1] == "--disable"
    assert capture["argv"][-1] == "-"
    assert "health.platform" in capture["stdin"]


@pytest.mark.parametrize(
    "kind",
    ["command_execution", "file_change", "mcp_tool_call", "web_search", "todo_list"],
)
def test_action_items_fail_closed(executable, bundle, events, kind):
    events[2]["item"]["type"] = kind
    executable(
        "sys.stdin.read()\n"
        + "\n".join("print(" + repr(json.dumps(item)) + ")" for item in events)
    )
    with pytest.raises(ValueError, match="analysis_unavailable"):
        analyst.run_analysis(bundle)


@pytest.mark.parametrize(
    "bad", ["line", "total", "message", "json", "usage", "missing"]
)
def test_malformed_or_oversized_output_is_rejected(executable, bundle, events, bad):
    if bad == "line":
        body = "print('x'*65537)"
    elif bad == "total":
        text = json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "r", "type": "reasoning", "text": "x" * 50000},
            }
        )
        body = (
            "print("
            + repr(json.dumps(events[0]))
            + ")\nprint("
            + repr(json.dumps(events[1]))
            + ")\nfor _ in range(24): print("
            + repr(text)
            + ")"
        )
    else:
        if bad == "message":
            events[3]["item"]["text"] = "x" * 65537
        elif bad == "json":
            events[3]["item"]["text"] = "not-json"
        elif bad == "usage":
            events[-1]["usage"]["input_tokens"] = True
        else:
            events.pop()
        body = "\n".join("print(" + repr(json.dumps(item)) + ")" for item in events)
    executable("sys.stdin.read()\n" + body)
    with pytest.raises(ValueError, match="analysis_unavailable"):
        analyst.run_analysis(bundle)


@pytest.mark.parametrize("field", ["size", "argv", "digest"])
def test_invalid_bundle_never_starts_cli(executable, bundle, field):
    directory = executable("open('started','w').write('1')")
    if field == "size":
        bundle["facts"]["health.platform"] = "x" * 65537
    elif field == "argv":
        bundle["argv"] = ["delete"]
    else:
        bundle["snapshot_digest"] = "invalid"
    with pytest.raises(ValueError) as error:
        analyst.run_analysis(bundle)
    assert isinstance(error.value, analyst._AnalysisError)
    assert error.value.exit_code == 21
    assert not (directory / "started").exists()


@pytest.mark.parametrize(
    "name", ["AGENTSPORE_API_KEY", "OPENAI_API_KEY", "SSH_AUTH_SOCK", "OPENAI_BASE_URL"]
)
def test_platform_key_environment_prevents_spawn(executable, bundle, monkeypatch, name):
    directory = executable("open('started','w').write('1')")
    monkeypatch.setenv(name, "synthetic-never-secret")
    with pytest.raises(ValueError):
        analyst.run_analysis(bundle)
    assert not (directory / "started").exists()


def test_timeout_kills_and_reaps_own_process(executable, bundle, monkeypatch):
    directory = executable(
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        "open('pid','w').write(str(os.getpid()))\n"
        "print(json.dumps({'type':'thread.started','thread_id':'ready'}),flush=True)\n"
        "while True: signal.pause()"
    )
    start = analyst.time.monotonic()
    monkeypatch.setattr(
        analyst.time,
        "monotonic",
        lambda: start + 119 if (directory / "pid").exists() else start,
    )
    monkeypatch.setattr(analyst, "TERM_SECONDS", 0.02)
    with pytest.raises(ValueError, match="analysis_unavailable") as error:
        analyst.run_analysis(bundle)
    assert isinstance(error.value, analyst._AnalysisError)
    assert error.value.exit_code == 29
    pid = int((directory / "pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.fixture
def connection(monkeypatch, bundle):
    raw = json.dumps(bundle).encode()
    pending = bytearray(struct.pack("!I", len(raw)) + raw)
    peer = MagicMock()
    peer.family = analyst.socket.AF_UNIX
    peer.getsockopt.return_value = struct.pack("3i", 321, 12345, 12345)

    def receive(size):
        count = min(size, 7)
        result = bytes(pending[:count])
        del pending[:count]
        return result

    peer.recv.side_effect = receive
    monkeypatch.setattr(analyst, "PEER_OPTION", 17)
    monkeypatch.setattr(
        analyst.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=12345)
    )
    return peer


def test_forged_peer_rejected_before_read(connection):
    connection.getsockopt.return_value = struct.pack("3i", 321, 999, 12345)
    with pytest.raises(ValueError):
        analyst.serve_connection(connection)
    connection.recv.assert_not_called()
    connection.sendall.assert_not_called()


def test_accepted_peer_receives_one_bounded_frame(connection, executable, events):
    executable(
        "sys.stdin.read()\n"
        + "\n".join("print(" + repr(json.dumps(item)) + ")" for item in events)
    )
    analyst.serve_connection(connection)
    response = connection.sendall.call_args.args[0]
    length = struct.unpack("!I", response[:4])[0]
    assert length == len(response) - 4 <= 65536
    assert json.loads(response[4:])["usage"]["output_tokens"] == 4


def test_oversized_socket_frame_never_reads_body(connection):
    connection.recv.side_effect = None
    connection.recv.return_value = struct.pack("!I", 65537)
    with pytest.raises(ValueError):
        analyst.serve_connection(connection)
    assert connection.recv.call_count == 1


def test_child_does_not_inherit_parent_descriptor(executable, bundle, events, tmp_path):
    descriptor = os.open(tmp_path / "capability", os.O_RDWR | os.O_CREAT, 0o600)
    os.set_inheritable(descriptor, True)
    inode = os.fstat(descriptor).st_ino
    try:
        directory = executable(
            "inherited=False\ntry:\n"
            + f" inherited=os.fstat({descriptor}).st_ino=={inode}\n"
            + "except OSError: inherited=False\n"
            + "open('descriptor.json','w').write(json.dumps(inherited))\n"
            + "sys.stdin.read()\n"
            + "\n".join("print(" + repr(json.dumps(item)) + ")" for item in events)
        )
        analyst.run_analysis(bundle)
        assert json.loads((directory / "descriptor.json").read_text()) is False
    finally:
        os.close(descriptor)


def test_whole_reply_limit_rejects_without_clipping(connection, executable, events):
    events[3]["item"]["text"] = json.dumps({"value": "x" * 65380})
    executable(
        "sys.stdin.read()\n"
        + "\n".join("print(" + repr(json.dumps(item)) + ")" for item in events)
    )
    with pytest.raises(ValueError, match="response_frame_too_large"):
        analyst.serve_connection(connection)
    connection.sendall.assert_not_called()


@pytest.mark.parametrize("kind", ["error", "turn.failed", "item.completed"])
@pytest.mark.parametrize(
    "failure",
    [
        ("model_not_found synthetic-private-detail", 24),
        ("Authentication failed synthetic-private-detail", 25),
        ("Usage limit exceeded synthetic-private-detail", 26),
        ("Network connection failed synthetic-private-detail", 27),
        ("Internal server error synthetic-private-detail", 28),
        ("Request timed out synthetic-private-detail", 29),
        ("unrecognized synthetic-private-detail", 24),
    ],
)
def test_native_errors_exit_with_finite_status_without_disclosure(
    executable, bundle, capfd, kind, failure
):
    message, code = failure
    event = {"type": kind}
    event["message" if kind == "error" else "error"] = (
        message if kind == "error" else {"message": message}
    )
    prefix = "sys.stdin.read()\n"
    if kind == "item.completed":
        prefix += (
            "print('"
            + json.dumps({"type": "thread.started", "thread_id": "synthetic"})
            + '\')\nprint(\'{"type":"turn.started"}\')\n'
        )
        event = {
            "type": kind,
            "item": {"id": "error_0", "type": "error", "message": message},
        }
    executable(prefix + "print(" + repr(json.dumps(event)) + ")")
    with pytest.raises(ValueError, match="^analysis_unavailable$") as error:
        analyst.run_analysis(bundle)
    assert isinstance(error.value, analyst._AnalysisError)
    assert error.value.exit_code == code
    assert capfd.readouterr() == ("", "")


@pytest.mark.parametrize(
    "failure", [("environment", 20), ("startup", 22), ("auth", 25), ("native", 42)]
)
def test_entrypoint_reports_fixed_status_without_raw_errors(
    connection, executable, monkeypatch, capfd, failure
):
    failure, code = failure
    body = "raise RuntimeError('synthetic-private-detail')"
    if failure == "auth":
        body = (
            "print('"
            + json.dumps(
                {
                    "type": "error",
                    "message": "Authentication failed synthetic-private-detail",
                }
            )
            + "')"
        )
    directory = executable("sys.stdin.read()\n" + body)
    if failure == "environment":
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic-private-detail")
    elif failure == "startup":
        monkeypatch.setattr(analyst, "ARGV", (str(directory / "absent-cli"),))
    connection.__enter__.return_value = connection
    monkeypatch.setattr(analyst.socket, "socket", lambda **kwargs: connection)
    monkeypatch.setattr(sys, "argv", ["operator_analyst"])
    with pytest.raises(SystemExit) as error:
        analyst.main()
    assert error.value.code == code
    connection.sendall.assert_not_called()
    assert capfd.readouterr() == ("", "")


@pytest.mark.parametrize("failure", [("peer", 1), ("arguments", 2)])
def test_entrypoint_preserves_peer_and_argument_rejection(
    connection, monkeypatch, failure
):
    kind, code = failure
    connection.getsockopt.return_value = struct.pack("3i", 321, 999, 12345)
    connection.__enter__.return_value = connection
    monkeypatch.setattr(analyst.socket, "socket", lambda **kwargs: connection)
    monkeypatch.setattr(
        sys,
        "argv",
        ["operator_analyst"] + (["unexpected"] if kind == "arguments" else []),
    )
    with pytest.raises(SystemExit) as error:
        analyst.main()
    assert error.value.code == code
    connection.recv.assert_not_called()
    connection.sendall.assert_not_called()


def test_malformed_error_event_remains_protocol_failure(executable, bundle):
    executable(
        "sys.stdin.read()\nprint('"
        + json.dumps(
            {
                "type": "error",
                "message": "Authentication failed",
                "command": "do-not-run",
            }
        )
        + "')"
    )
    with pytest.raises(ValueError) as error:
        analyst.run_analysis(bundle)
    assert isinstance(error.value, analyst._AnalysisError)
    assert error.value.exit_code == 40


@pytest.mark.parametrize(
    "failure",
    [
        ("json", 30),
        ("bounds", 31),
        ("schema", 32),
        ("action", 33),
        ("usage", 34),
        ("incomplete", 35),
        ("final", 36),
        ("exit", 37),
    ],
)
def test_native_failure_boundaries_have_distinct_finite_status(
    executable, bundle, events, capfd, failure
):
    failure, code = failure
    if failure == "json":
        body = "print('synthetic-private-detail')"
    elif failure == "bounds":
        body = "print('x'*65537)"
    else:
        if failure == "schema":
            events[0]["unexpected"] = "synthetic-private-detail"
        elif failure == "action":
            events[2]["item"]["type"] = "command_execution"
        elif failure == "usage":
            events[-1]["usage"]["input_tokens"] = True
        elif failure == "incomplete":
            events.pop()
        elif failure == "final":
            events[3]["item"]["text"] = "synthetic-private-detail"
        body = "\n".join("print(" + repr(json.dumps(item)) + ")" for item in events)
        if failure == "exit":
            body += "\nsys.exit(9)"
    executable("sys.stdin.read()\n" + body)
    with pytest.raises(ValueError, match="^analysis_unavailable$") as error:
        analyst.run_analysis(bundle)
    assert isinstance(error.value, analyst._AnalysisError)
    assert error.value.exit_code == code
    assert capfd.readouterr() == ("", "")


@pytest.mark.parametrize(
    "diagnostic,code",
    [
        ("error loading config", 43),
        ("unexpected argument", 43),
        ("Permission denied", 44),
        ("Authentication failed", 25),
        ("Usage limit exceeded", 26),
        ("Network connection failed", 27),
        ("Internal server error", 28),
        ("Request timed out", 29),
        ("unrecognized", 42),
    ],
)
def test_early_native_failure_classifies_bounded_stderr_only(
    executable, bundle, capfd, diagnostic, code
):
    executable(
        "sys.stdin.read()\nprint("
        + repr(diagnostic + " synthetic-private-detail" * 1000)
        + ",file=sys.stderr)\nsys.exit(9)"
    )
    with pytest.raises(ValueError, match="^analysis_unavailable$") as error:
        analyst.run_analysis(bundle)
    assert isinstance(error.value, analyst._AnalysisError)
    assert error.value.exit_code == code
    assert capfd.readouterr() == ("", "")


def test_successful_empty_stream_does_not_trust_stderr(executable, bundle, capfd):
    executable("sys.stdin.read()\nprint('Authentication failed',file=sys.stderr)")
    with pytest.raises(ValueError) as error:
        analyst.run_analysis(bundle)
    assert isinstance(error.value, analyst._AnalysisError)
    assert error.value.exit_code == 35
    assert capfd.readouterr() == ("", "")


def test_cleanup_failure_has_distinct_status(executable, bundle, events, monkeypatch):
    executable(
        "sys.stdin.read()\n"
        + "\n".join("print(" + repr(json.dumps(item)) + ")" for item in events)
    )
    original = analyst._terminate

    def reject_after_reap(process):
        original(process)
        raise ValueError("synthetic-private-detail")

    monkeypatch.setattr(analyst, "_terminate", reject_after_reap)
    with pytest.raises(ValueError, match="^analysis_unavailable$") as error:
        analyst.run_analysis(bundle)
    assert isinstance(error.value, analyst._AnalysisError)
    assert error.value.exit_code == 39


def test_early_failure_drains_large_stderr_without_reading_stdin(
    executable, bundle, capfd, monkeypatch
):
    observed = []
    original = analyst._early_failure

    def classify(diagnostic):
        observed.append(len(diagnostic))
        return original(diagnostic)

    monkeypatch.setattr(analyst, "_early_failure", classify)
    bundle["facts"]["health.platform"] = "x" * 50000
    executable(
        "sys.stderr.write('error loading config ' + 'synthetic-private-detail'*10000)\n"
        "sys.exit(9)"
    )
    with pytest.raises(ValueError, match="^analysis_unavailable$") as error:
        analyst.run_analysis(bundle)
    assert isinstance(error.value, analyst._AnalysisError)
    assert error.value.exit_code == 43
    assert observed == [8192]
    assert capfd.readouterr() == ("", "")

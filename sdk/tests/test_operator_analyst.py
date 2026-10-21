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
    assert "network={enabled=false}" in " ".join(capture["argv"])
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
    with pytest.raises(ValueError):
        analyst.run_analysis(bundle)
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
    with pytest.raises(ValueError, match="analysis_unavailable"):
        analyst.run_analysis(bundle)
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

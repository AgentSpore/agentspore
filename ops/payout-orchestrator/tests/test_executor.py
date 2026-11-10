import os
import stat
import subprocess

import pytest

from payout_orchestrator.config import ConfigError, load_config
from payout_orchestrator.executor import DockerExecutor, ExecutorError, parse_verdict, write_private


class Runner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.argv = []
        self.stdin = []

    def __call__(self, argv, **kwargs):
        self.argv.append(argv)
        self.stdin.append(kwargs.get("input"))
        out, code = self.outputs.pop(0)
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr=b"")


@pytest.fixture
def runner(monkeypatch):
    fake = Runner([])
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def test_cli_runs_docker_exec_and_branches_on_json_not_exit_code(runner):
    runner.outputs = [(b'{"verdict": "claimed", "epoch": 3}\n', 9)]
    result = DockerExecutor("c1", 5).cli("payout_delivery", ["claim"], '{"a": 1}')
    assert result["verdict"] == "claimed"
    assert runner.argv[0] == [
        "docker",
        "exec",
        "-i",
        "c1",
        "python",
        "-m",
        "app.cli.payout_delivery",
        "claim",
    ]
    assert runner.stdin[0] == b'{"a": 1}'


@pytest.mark.parametrize(
    "raw", [b"", b"not json", b'{"verdict": "a b"}', b'{"verdict": "x://u:p@h"}']
)
def test_parse_verdict_rejects_unsafe_output(raw):
    with pytest.raises(ExecutorError):
        parse_verdict(raw)


def show(code="1", status="13", result="exit-code"):
    return f"ExecMainCode={code}\nExecMainStatus={status}\nResult={result}\n".encode()


def test_sign_reads_exec_main_status(runner):
    runner.outputs = [(b"", 1), (show(), 0)]
    assert DockerExecutor("c1", 5).sign("abc.2") == 13
    assert runner.argv[0] == ["systemctl", "start", "payout-signer@abc.2.service"]
    assert runner.argv[1][:2] == ["systemctl", "show"]
    assert runner.argv[1][3] == "ExecMainCode,ExecMainStatus,Result"


@pytest.mark.parametrize(
    "out",
    [
        show(code="0", status="0", result="success"),  # unit never ran
        show(code="2", status="9"),  # killed by a signal: status is a signal number
        show(result="timeout"),
        show(status="x"),
        b"",
    ],
)
def test_sign_status_untrusted_unless_normal_exit(runner, out):
    runner.outputs = [(b"", 1), (out, 0)]
    with pytest.raises(ExecutorError):
        DockerExecutor("c1", 5).sign("abc.2")


def test_handoff_file_mode_is_0640(tmp_path):
    previous = os.umask(0)
    try:
        write_private(tmp_path / "f", b"x")
    finally:
        os.umask(previous)
    assert stat.S_IMODE((tmp_path / "f").stat().st_mode) == 0o640


def test_timeout_becomes_executor_error(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("x", 1)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(ExecutorError):
        DockerExecutor("c1", 1).cli("payout_delivery", ["pending"], "")


def test_load_config_toml_and_unknown_keys(tmp_path):
    path = tmp_path / "c.toml"
    base = (
        'container = "backend-placeholder"\nworker_id = "w"\nhandoff_dir = "/h"\n'
        'journal_path = "/j"\npause_path = "/p"\nper_tick = 3\n'
    )
    path.write_text(base)
    assert load_config(path).per_tick == 3
    path.write_text(base + "extra = 1\n")
    with pytest.raises(ConfigError):
        load_config(path)
    path.write_text(base.replace("per_tick = 3\n", ""))
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize(
    ("out", "expected"),
    [
        (b'{"verdict": "found", "height": 77}', 77),
        (b'{"verdict": "unavailable", "height": 77}', None),
        (b'{"verdict": "found", "height": true}', None),
        (b'{"verdict": "found"}', None),
        (b"garbage", None),
    ],
)
def test_block_height_contract(runner, out, expected):
    runner.outputs = [(out, 0)]
    assert DockerExecutor("c1", 5).block_height() == expected
    assert runner.argv[0][-2:] == ["app.cli.payout_delivery", "block-height"]
    assert runner.stdin[0] == b"{}"

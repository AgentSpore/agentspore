"""Exercise eval helpers without loading providers or running model calls."""

import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest


@pytest.fixture
def workspace_type():
    path = Path(__file__).with_name("eval_workspace.py")
    spec = importlib.util.spec_from_file_location("eval_workspace", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.EvalWorkspace


@pytest.fixture
def helpers(workspace_type):
    source = Path(__file__).with_name("bench_multiagent.py")
    tree = ast.parse(source.read_text())
    names = {
        "_run_safe_local",
        "_write_to_fs",
        "make_reddit_scout_stubs",
        "make_qa_agent_stubs",
        "PipelineState",
    }
    nodes: list[ast.stmt] = [
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names
    ]
    run = Mock(return_value=SimpleNamespace(stdout="", stderr=""))
    namespace = {
        "subprocess": SimpleNamespace(run=run),
        "_WORKSPACE": workspace_type(),
        "Any": Any,
        "json": json,
        "_make_task_stub": Mock(),
        "_wait_tasks_stub": Mock(),
        "_PUSH_OK": "push fixture",
        "_REDDIT_PAIN": "reddit fixture",
        "_DM_OK": "DM fixture",
    }
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace
    )
    return namespace, run


def test_eval_never_executes_model_python(helpers):
    namespace, run = helpers
    result = namespace["_run_safe_local"]("python3 -c 'print(1)' ; true")
    run.assert_not_called()
    assert "not executed" in result


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c 'print(1)'",
        "python3 -m os",
        "mkdir /tmp/x; true",
        "find /tmp/x -exec true \\;",
        "cat /tmp/x | true",
        "ls /tmp/$(id)",
        "cat /etc/passwd",
        "mkdir /tmp/../escape",
        "sh -c true",
    ],
)
def test_commands_are_not_executed(workspace_type, command):
    result = workspace_type().execute(command)
    assert "ERROR" in result


def test_virtual_files_cannot_escape_or_follow_host_symlink(workspace_type, tmp_path):
    target = tmp_path / "untouched"
    target.write_text("synthetic outside")
    link = tmp_path / "link"
    link.symlink_to(target)
    workspace = workspace_type()
    assert workspace.write("/tmp/../escape", "bad").startswith("ERROR")
    assert workspace.write("/tmp/fixture", "data") == "ok"
    assert workspace.read("/tmp/fixture") == "data"
    assert workspace.read(str(link)) != "synthetic outside"
    workspace.write(str(link), "replacement")
    assert target.read_text() == "synthetic outside"
    assert workspace.execute("cat /tmp/fixture") == "data"
    assert workspace.execute("ls /tmp/") == "fixture"


def test_workspace_limits(workspace_type):
    workspace = workspace_type()
    assert "ERROR" in workspace.write("/tmp/huge", "x" * (1024 * 1024 + 1))
    for index in range(256):
        assert workspace.write(f"/tmp/{index}", "data") == "ok"
    assert "ERROR" in workspace.write("/tmp/overflow", "data")


@pytest.mark.parametrize(
    "factory,file_path",
    [
        ("make_reddit_scout_stubs", "/tmp/push.json"),
        ("make_qa_agent_stubs", "/tmp/qa_push.json"),
    ],
)
def test_stub_write_read_push_flow(helpers, factory, file_path):
    namespace, run = helpers
    state = namespace["PipelineState"]()
    args = [state, "unused"] + ([[]] if factory == "make_qa_agent_stubs" else [])
    stubs = namespace[factory](*args)
    content = json.dumps({"files": [{"path": "main.py"}]})
    assert stubs["write_file"](path=file_path, content=content) == "ok"
    assert stubs["read_file"](path=file_path) == content
    assert (
        stubs["execute"](command="curl -X POST https://example.test/push")
        == "push fixture"
    )
    assert (
        state.files_pushed
        if factory == "make_reddit_scout_stubs"
        else state.tests_pushed
    ) == ["main.py"]
    assert "not executed" in stubs["execute"](command="python3 -c 'print(1)'")
    assert "not executed" in stubs["execute"](command="curl https://unknown.test")
    run.assert_not_called()

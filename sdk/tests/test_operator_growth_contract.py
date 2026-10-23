"""Synthetic proposal boundaries and real dependency-free Node prototype tests."""

import copy
import hashlib
import json
import shutil
import sys
import time

import pytest

from agentspore_sdk import operator_growth_contract as contract
from agentspore_sdk.operator_growth_contract import (
    parse_growth,
    service_digest,
    validate_service,
    verify_prototype,
)


@pytest.fixture(autouse=True)
def local_node(monkeypatch, tmp_path):
    node = shutil.which("node")
    assert node, "Node is required for the real prototype fixture"
    monkeypatch.setattr(contract, "NODE", node)
    wrapper = tmp_path / "functional-only-wrapper"
    wrapper.write_text(
        "#!" + sys.executable + "\nimport os,sys\n"
        "args=sys.argv[sys.argv.index('/node/bin/node')+1:]\n"
        "os.execv(" + repr(node) + ", [" + repr(node) + "]+args)\n"
    )
    wrapper.chmod(0o700)
    monkeypatch.setattr(contract, "BWRAP", str(wrapper))


@pytest.fixture
def bundle():
    return {
        "snapshot_digest": "a" * 64,
        "window": {},
        "purpose": "growth",
        "facts": {
            "demand.one": "Need a list",
            "demand.two": "Need a list too",
            "agent.one": "Agent",
        },
        "coverage": {
            "source_kinds": {
                "demand.one": "demand",
                "demand.two": "demand",
                "agent.one": "agent",
            },
            "source_urls": {
                "demand.one": "https://example.org/a",
                "demand.two": "https://example.org/b",
            },
            "project_titles": ["Existing"],
        },
    }


@pytest.fixture
def service():
    return {
        "title": "family-list",
        "description": "A useful family list",
        "source_ids": ["demand.one", "demand.two"],
        "files": [
            {
                "path": "index.html",
                "content": "<!doctype html><title>Family list</title>",
            },
            {"path": "README.md", "content": "Unpublished prototype."},
            {"path": "list.js", "content": "exports.add = (xs, x) => [...xs, x];"},
            {
                "path": "tests/list.test.js",
                "content": "const {test}=require('node:test'); const a=require('node:assert/strict'); const {add}=require('../list.js'); test('add preserves existing items',()=>a.deepEqual(add(['a'],'b'),['a','b']));",
            },
        ],
    }


@pytest.fixture
def proposal(bundle, service):
    return {
        "snapshot_digest": bundle["snapshot_digest"],
        "ideas": [
            {
                "evidence_ids": service["source_ids"],
                "decision": "create",
                "reason": "Useful",
                "target_user": "Families",
            }
        ],
        "candidates": [
            {
                "evidence_id": "agent.one",
                "fit": "Builds tools",
                "invitation_draft": "Consider joining",
            }
        ],
        "service": service,
    }


def test_strict_contract_and_digest(bundle, proposal, service):
    assert parse_growth(json.dumps(proposal), bundle) == proposal
    assert validate_service(service) is service
    reordered = dict(reversed(list(service.items())))
    assert service_digest(reordered) == service_digest(service)
    altered = copy.deepcopy(service)
    altered["files"][0]["content"] += " changed"
    assert service_digest(altered) != service_digest(service)


@pytest.mark.parametrize(
    "raw", ['{"a":1,"a":2}', "NaN", '{"a":1e999}', "[]", '"\\ud800"']
)
def test_invalid_json(bundle, raw):
    with pytest.raises(ValueError):
        parse_growth(raw, bundle)


@pytest.mark.parametrize(
    "path",
    [
        "../index.html",
        "/index.html",
        ".env",
        "https://example.org/a.js",
        "package.json",
        "tests/../a.js",
        "a/b.js",
        "tests/A.test.js",
    ],
)
def test_forbidden_manifest_paths(service, path):
    service["files"][0]["path"] = path
    with pytest.raises(ValueError):
        verify_prototype(service, time.monotonic() + 2)


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_url",
        "wrong_kind",
        "duplicate_title",
        "create_missing",
        "create_mismatch",
        "model_receipt",
        "unknown_candidate",
    ],
)
def test_evidence_and_creation_guards(bundle, proposal, change):
    if change == "duplicate_url":
        bundle["coverage"]["source_urls"]["demand.two"] = "https://example.org/a"
    elif change == "wrong_kind":
        bundle["coverage"]["source_kinds"]["demand.two"] = "project"
    elif change == "duplicate_title":
        bundle["coverage"]["project_titles"] = ["FAMILY-LIST"]
    elif change == "create_missing":
        proposal["ideas"] = []
    elif change == "create_mismatch":
        proposal["ideas"][0]["evidence_ids"] = ["demand.one"]
    elif change == "model_receipt":
        proposal["verification"] = {"tests_passed": 1}
    else:
        proposal["candidates"][0]["evidence_id"] = "demand.one"
    with pytest.raises(ValueError):
        parse_growth(json.dumps(proposal), bundle)


def test_native_functional_tests_and_minimal_environment(service, monkeypatch):
    assert shutil.which("node"), "Node is required for the real prototype fixture"
    monkeypatch.setenv("SYNTHETIC_CREDENTIAL", "must-not-propagate")
    service["files"][-1]["content"] += (
        " test('environment',()=>a.equal(process.env.SYNTHETIC_CREDENTIAL,undefined));"
    )
    receipt = verify_prototype(service, time.monotonic() + 5)
    assert receipt == {"service_digest": service_digest(service), "tests_passed": 2}


@pytest.mark.parametrize(
    "body,code",
    [
        (
            "const {test}=require('node:test'); test('fails',()=>{throw Error('expected');});",
            "growth_tests_failed",
        ),
        (
            "const {test}=require('node:test'); test.skip('skipped',()=>{});",
            "growth_tests_receipt_invalid",
        ),
        ("while(true){}", "growth_tests_timeout"),
        ("process.stdout.write('x'.repeat(70000));", "growth_tests_output_limit"),
    ],
)
def test_native_failure_skipped_hang_and_flood(service, body, code):
    service["files"][-1]["content"] = body
    with pytest.raises(ValueError, match=code):
        verify_prototype(
            service, time.monotonic() + (0.5 if code == "growth_tests_timeout" else 3)
        )


def test_missing_node_and_expired_deadline(service, monkeypatch):
    monkeypatch.setattr(contract, "NODE", "/nonexistent/agentspore-node")
    with pytest.raises(ValueError, match="growth_node_unavailable"):
        verify_prototype(service, time.monotonic() + 1)
    with pytest.raises(ValueError, match="growth_tests_timeout"):
        verify_prototype(service, time.monotonic() - 1)


def test_isolation_required_and_fixed_mounts(service, monkeypatch):
    argv = contract._prototype_argv("/tmp/prototype", ["tests/list.test.js"])
    assert "--unshare-all" in argv and "--clearenv" in argv
    assert argv[-4:] == [
        "/node/bin/node",
        "--test",
        "--test-reporter=tap",
        "tests/list.test.js",
    ]
    assert "/home" not in argv and "/etc" not in argv
    mount = argv.index("/work")
    assert argv[mount - 2 : mount + 1] == ["--ro-bind", "/tmp/prototype", "/work"]
    assert "--bind" not in argv
    monkeypatch.setattr(contract, "BWRAP", "/nonexistent/agentspore-bwrap")
    with pytest.raises(ValueError, match="growth_test_isolation_unavailable"):
        verify_prototype(service, time.monotonic() + 1)


@pytest.mark.parametrize(
    "mutation",
    [
        "fs.writeFileSync('list.js','exports.add=()=>[];')",
        "fs.unlinkSync('list.js');fs.symlinkSync('index.html','list.js')",
    ],
)
def test_native_rewrite_cannot_receive_original_manifest_receipt(service, mutation):
    original = copy.deepcopy(service)
    service["files"][-1]["content"] = (
        "const {test}=require('node:test'); const fs=require('node:fs');"
        "test('rewrite broken app',()=>{" + mutation + "});"
    )
    with pytest.raises(ValueError, match="growth_files_changed"):
        verify_prototype(service, time.monotonic() + 5)
    assert service["files"][:-1] == original["files"][:-1]


def test_receipt_matches_exact_original_manifest_bytes(service):
    original = copy.deepcopy(service)
    expected = hashlib.sha256(
        json.dumps(
            original, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    receipt = verify_prototype(service, time.monotonic() + 5)
    assert receipt == {"service_digest": expected, "tests_passed": 1}
    assert service == original

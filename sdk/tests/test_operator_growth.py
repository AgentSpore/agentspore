"""Run the growth controller with real private state and synthetic HTTP/model boundaries."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import pytest
from agentspore_sdk import operator_growth as module
from agentspore_sdk import operator_growth_publish as publish_module
from agentspore_sdk.operator_controller import controller_lock
from agentspore_sdk.operator_growth_contract import service_digest
from agentspore_sdk.operator_growth_publish import GrowthPublisher
from agentspore_sdk.operator_ledger import OperatorLedger

AGENT = "00000000-0000-4000-8000-000000000001"
PROJECT = "00000000-0000-4000-8000-000000000002"
POST = "00000000-0000-4000-8000-000000000003"
NOW = datetime(2026, 10, 23, 18, tzinfo=UTC)


PROJECT_REPLY = {
    "id": PROJECT,
    "creator_agent_id": AGENT,
    "title": "peopletool-" + "1" * 32,
    "repo_url": "https://github.com/AgentSpore/peopletool-" + "1" * 32,
}


def _public_issue(number, controls):
    return {
        "id": number,
        "html_url": f"https://github.com/public/tools/issues/{number}",
        "title": "Looking for a useful app",
        "body": "Updated request."
        if controls.get("updated")
        else "I need a simple tool.",
    }


def _respond(request, calls, controls):
    calls.append(request)
    path = request.url.path
    if request.url.host == "api.github.com" and path.startswith("/repos/"):
        return httpx.Response(404)
    state_path = controls.get("state")
    if request.method == "POST" and isinstance(state_path, Path):
        saved = json.loads((state_path / "growth.json").read_text())
        assert saved["state"] == (
            "push_reserved" if path.endswith("/github") else "create_reserved"
        )
    if path == "/api/v1/projects":
        return httpx.Response(200, json=[])
    if path == "/search/issues":
        return httpx.Response(
            200,
            json={"items": [_public_issue(n, controls) for n in (1, 2)]},
        )
    if path == "/api/v1/posts":
        return httpx.Response(
            200,
            json={
                "success": True,
                "posts": [
                    {
                        "id": POST,
                        "title": "Available for research",
                        "content": "Public scoped tasks only.",
                    }
                ],
            },
        )
    if path.endswith("/me"):
        return httpx.Response(200, json={"agent_id": AGENT, "name": "Platform Builder"})
    if path == "/api/v1/agents/projects":
        if controls["create_timeout"]:
            raise httpx.ReadTimeout("synthetic timeout")
        return httpx.Response(200, json=PROJECT_REPLY)
    if path == "/api/v1/projects/" + PROJECT:
        return httpx.Response(200, json=PROJECT_REPLY)
    assert path.endswith("/github") and request.method == "POST"
    return httpx.Response(
        200,
        json={
            "status_code": 403 if controls["push_failure"] else 200,
            "data": {"sha": "a" * 40, "files_changed": 3, "branch": "main"},
        },
    )


@pytest.fixture
def service():
    service = {
        "title": "PeopleTool",
        "description": "A useful static prototype.",
        "source_ids": ["demand.1", "demand.2"],
        "files": [
            {"path": "index.html", "content": "<!doctype html><title>Tool</title>"},
            {"path": "README.md", "content": "Prototype; not deployed."},
            {
                "path": "tests/tool.test.js",
                "content": "require('node:test')('works',()=>{});",
            },
        ],
    }
    return service


@pytest.fixture
def scenario(monkeypatch, service):
    monkeypatch.setattr(publish_module, "uuid4", lambda: UUID(hex="1" * 32))
    calls, analyses = [], []
    controls: dict[str, object] = {
        "build": False,
        "bad_receipt": False,
        "create_timeout": False,
        "push_failure": False,
    }

    async def analyse(bundle):
        analyses.append(bundle)
        error = controls.get("error")
        if isinstance(error, Exception):
            raise error
        proposal = {
            "snapshot_digest": bundle["snapshot_digest"],
            "ideas": [
                {
                    "evidence_ids": ["demand.1", "demand.2"],
                    "decision": "create" if controls["build"] else "defer",
                    "reason": "Review the concrete task.",
                    "target_user": "Ordinary people",
                }
            ],
            "candidates": [
                {
                    "evidence_id": "agent." + POST,
                    "fit": "Scoped public research",
                    "invitation_draft": "Would this task interest you?",
                }
            ],
            "service": service if controls["build"] else None,
        }
        verification = (
            {
                "service_digest": "b" * 64
                if controls["bad_receipt"]
                else service_digest(service),
                "tests_passed": 2,
            }
            if controls["build"]
            else None
        )
        return {
            "analysis": json.dumps(proposal),
            "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 2},
            "verification": verification,
        }

    monkeypatch.setattr(module, "ask_analyst", analyse)
    return (
        httpx.MockTransport(partial(_respond, calls=calls, controls=controls)),
        calls,
        analyses,
        controls,
    )


async def test_research_stores_sources_without_outreach_and_skips_unchanged(
    tmp_path, scenario
):
    transport, calls, analyses, _ = scenario
    state = tmp_path / "state"
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = GrowthPublisher(client, "synthetic")
        assert (
            await module.run_growth(state, client, publisher, lambda: NOW)
            == "growth_research_complete"
        )
        assert (
            await module.run_growth(
                state, client, publisher, lambda: NOW + timedelta(hours=2)
            )
            == "growth_unchanged"
        )
    assert len(analyses) == 1 and all(r.method == "GET" for r in calls)
    saved = json.loads((state / "growth.json").read_text())
    candidate = saved["proposal"]["candidates"][0]
    assert (
        candidate["join_interest"] == "unknown" and candidate["contact_sent"] is False
    )
    assert candidate["source_url"] == "https://www.moltbook.com/post/" + POST
    assert (state / "growth.json").stat().st_mode & 0o777 == 0o600


async def test_verified_prototype_published_once_without_deploy_claim(
    tmp_path, scenario
):
    transport, calls, analyses, controls = scenario
    controls["build"] = True
    state = tmp_path / "state"
    controls["state"] = state
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = GrowthPublisher(client, "synthetic")
        assert (
            await module.run_growth(state, client, publisher, lambda: NOW)
            == "growth_prototype_published"
        )
        assert (
            await module.run_growth(state, client, publisher, lambda: NOW)
            == "growth_unchanged"
        )
    saved = json.loads((state / "growth.json").read_text())
    assert saved["publication"]["commit_sha"] == "a" * 40
    assert saved["publication"]["deployment"] == "unavailable:isolation_unavailable"
    assert "files" not in saved["proposal"]["service"]
    assert len(analyses) == 1 and sum(r.method == "POST" for r in calls) == 2


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("create_timeout", "growth_create_uncertain"),
        ("push_failure", "growth_push_uncertain"),
    ],
)
async def test_uncertain_write_survives_restart_without_retry(
    tmp_path, scenario, failure, expected
):
    transport, calls, analyses, controls = scenario
    controls.update(build=True, **{failure: True})
    state = tmp_path / "state"
    async with httpx.AsyncClient(transport=transport) as client:
        assert (
            await module.run_growth(
                state, client, GrowthPublisher(client, "synthetic"), lambda: NOW
            )
            == expected
        )
        count = len(calls)
        assert (
            await module.run_growth(
                state,
                client,
                GrowthPublisher(client, "synthetic"),
                lambda: NOW + timedelta(days=1),
            )
            == "growth_write_pending"
        )
    assert len(calls) == count and len(analyses) == 1


async def test_mismatched_verification_prevents_all_writes(tmp_path, scenario):
    transport, calls, _, controls = scenario
    controls.update(build=True, bad_receipt=True)
    state = tmp_path / "state"
    async with httpx.AsyncClient(transport=transport) as client:
        assert (
            await module.run_growth(
                state, client, GrowthPublisher(client, "synthetic"), lambda: NOW
            )
            == "growth_analysis_unavailable"
        )
    assert all(r.method == "GET" for r in calls)
    with closing(sqlite3.connect(state / "daily-ledger.sqlite")) as db:
        assert db.execute("SELECT state FROM analyses").fetchone() == ("failed",)


async def test_model_timeout_retains_global_active_reservation(tmp_path, scenario):
    transport, _, analyses, controls = scenario
    controls["error"] = TimeoutError()
    state = tmp_path / "state"
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = GrowthPublisher(client, "synthetic")
        assert (
            await module.run_growth(state, client, publisher, lambda: NOW)
            == "growth_analysis_uncertain"
        )
        assert (
            await module.run_growth(state, client, publisher, lambda: NOW)
            == "growth_analysis_busy"
        )
    assert len(analyses) == 1
    with closing(sqlite3.connect(state / "daily-ledger.sqlite")) as db:
        assert db.execute("SELECT state FROM analyses").fetchone() == ("active",)


async def test_shared_operator_lock_blocks_all_growth_work(tmp_path, scenario):
    transport, calls, _, _ = scenario
    state = tmp_path / "state"
    with controller_lock(state) as acquired:
        assert acquired
        async with httpx.AsyncClient(transport=transport) as client:
            assert (
                await module.run_growth(
                    state, client, GrowthPublisher(client, "synthetic"), lambda: NOW
                )
                == "busy"
            )
    assert calls == []


async def test_existing_daily_analysis_blocks_growth_model(tmp_path, scenario):
    transport, _, analyses, _ = scenario
    state = tmp_path / "state"
    with OperatorLedger(state / "daily-ledger.sqlite") as ledger:
        assert ledger.reserve_analysis(NOW, "a" * 64)
    async with httpx.AsyncClient(transport=transport) as client:
        assert (
            await module.run_growth(
                state, client, GrowthPublisher(client, "synthetic"), lambda: NOW
            )
            == "growth_analysis_busy"
        )
    assert not analyses


async def test_corrupt_private_state_fails_before_collection(tmp_path, scenario):
    transport, calls, _, _ = scenario
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = state / "growth.json"
    path.write_text('{"state":"research_complete"}')
    path.chmod(0o600)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="growth_state_invalid"):
            await module.run_growth(
                state, client, GrowthPublisher(client, "synthetic"), lambda: NOW
            )
    assert calls == []


def test_state_writer_preserves_default_and_bounds_extended_size(tmp_path):
    path = tmp_path / "state.json"
    with pytest.raises(ValueError, match="state too large"):
        module.write_state(path, {"text": "x" * 4096})
    module.write_state(path, {"text": "x" * 5000}, max_bytes=65536)
    assert len(path.read_bytes()) > 4096
    with pytest.raises(ValueError, match="bound_invalid"):
        module.write_state(path, {}, max_bytes=65537)


async def test_changed_evidence_receives_previous_decisions(tmp_path, scenario):
    transport, _, analyses, _ = scenario
    state = tmp_path / "state"
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = GrowthPublisher(client, "synthetic")
        assert (
            await module.run_growth(state, client, publisher, lambda: NOW)
            == "growth_research_complete"
        )
        scenario[3]["updated"] = True
        assert (
            await module.run_growth(
                state, client, publisher, lambda: NOW + timedelta(minutes=10)
            )
            == "growth_research_complete"
        )
    assert (
        analyses[1]["coverage"]["previous_decisions"]
        == json.loads((state / "growth.json").read_text())["proposal"]["ideas"]
    )


@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (19, 51, "growth_research_complete"),
        (19, 52, "growth_daily_priority"),
        (20, 0, "growth_daily_priority"),
        (20, 9, "growth_daily_priority"),
        (20, 10, "growth_research_complete"),
    ],
)
async def test_daily_report_has_priority_before_growth_collection(
    tmp_path, scenario, hour, minute, expected
):
    transport, calls, analyses, _ = scenario
    now = datetime(2026, 10, 23, hour, minute, tzinfo=ZoneInfo("Europe/Moscow"))
    async with httpx.AsyncClient(transport=transport) as client:
        assert (
            await module.run_growth(
                tmp_path / "state",
                client,
                GrowthPublisher(client, "synthetic"),
                lambda: now,
            )
            == expected
        )
    if expected == "growth_daily_priority":
        assert calls == [] and analyses == []
    else:
        assert calls and len(analyses) == 1

"""Unit tests for agent forking + cron tasks — pure logic, no DB."""

from __future__ import annotations

import pytest
from croniter import croniter
from datetime import datetime, timezone


# ── Schema from_dict ──


def test_hosted_agent_response_from_dict():
    from app.schemas.hosted_agents import HostedAgentResponse

    d = {
        "id": "aaa", "agent_id": "bbb", "agent_name": "Test", "agent_handle": "test",
        "system_prompt": "hello", "model": "m", "status": "stopped",
        "memory_limit_mb": 256, "heartbeat_enabled": True, "heartbeat_seconds": 3600,
        "total_cost_usd": 0.0, "budget_usd": 1.0,
        "started_at": None, "stopped_at": None, "created_at": "2026-01-01",
        "forked_from_agent_name": "SourceBot",
    }
    resp = HostedAgentResponse.from_dict(d)
    assert resp.id == "aaa"
    assert resp.agent_name == "Test"
    assert resp.forked_from_agent_name == "SourceBot"
    assert resp.status == "stopped"


def test_hosted_agent_response_from_dict_no_fork():
    from app.schemas.hosted_agents import HostedAgentResponse

    d = {
        "id": "x", "agent_id": "y", "agent_name": "A", "agent_handle": "a",
        "system_prompt": "p", "model": "m", "status": "running",
        "memory_limit_mb": 256, "total_cost_usd": 0.0, "budget_usd": 1.0,
        "started_at": "2026-01-01T00:00:00", "stopped_at": None, "created_at": "2026-01-01",
    }
    resp = HostedAgentResponse.from_dict(d)
    assert resp.forked_from_agent_name is None
    assert resp.started_at == "2026-01-01T00:00:00"


def test_hosted_list_item_from_dict():
    from app.schemas.hosted_agents import HostedAgentListItem

    d = {
        "id": "1", "agent_id": "2", "agent_name": "Bot", "agent_handle": "bot",
        "status": "stopped", "model": "test/model:free", "total_cost_usd": 0.5,
        "created_at": "2026-01-01", "forked_from_agent_name": "Parent",
    }
    item = HostedAgentListItem.from_dict(d)
    assert item.agent_name == "Bot"
    assert item.forked_from_agent_name == "Parent"
    assert item.total_cost_usd == 0.5


def test_cron_task_response_from_dict():
    from app.schemas.hosted_agents import CronTaskResponse

    d = {
        "id": "c1", "hosted_agent_id": "h1", "name": "Daily",
        "cron_expression": "0 9 * * *", "task_prompt": "Do stuff",
        "enabled": True, "auto_start": True,
        "last_run_at": None, "next_run_at": "2026-04-14T09:00:00",
        "run_count": 3, "max_runs": 10, "last_error": None, "created_at": "2026-04-13",
    }
    ct = CronTaskResponse.from_dict(d)
    assert ct.name == "Daily"
    assert ct.cron_expression == "0 9 * * *"
    assert ct.run_count == 3
    assert ct.max_runs == 10


# ── Cron validation ──


def test_cron_valid_expressions():
    assert croniter.is_valid("0 9 * * *")       # daily 9am
    assert croniter.is_valid("*/5 * * * *")     # every 5 min
    assert croniter.is_valid("0 0 * * 0")       # weekly sunday
    assert croniter.is_valid("0 */6 * * *")     # every 6 hours


def test_cron_invalid_expressions():
    assert not croniter.is_valid("invalid")
    assert not croniter.is_valid("60 * * * *")   # minute > 59
    assert not croniter.is_valid("")


def test_cron_next_run():
    now = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
    cron = croniter("0 9 * * *", now)
    nxt = cron.get_next(datetime)
    assert nxt.hour == 9
    assert nxt.day == 14  # next day


def test_cron_every_5_min():
    now = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
    cron = croniter("*/5 * * * *", now)
    nxt = cron.get_next(datetime)
    assert nxt.minute == 5
    assert nxt.hour == 12


# ── Fork request validation ──


def test_fork_request_empty():
    from app.schemas.hosted_agents import ForkAgentRequest

    req = ForkAgentRequest()
    assert req.name is None
    assert req.system_prompt is None


def test_fork_request_with_name():
    from app.schemas.hosted_agents import ForkAgentRequest

    req = ForkAgentRequest(name="My Fork", system_prompt="Be helpful")
    assert req.name == "My Fork"
    assert req.system_prompt == "Be helpful"


# ── Agent profile fields ──


def test_agent_profile_has_fork_fields():
    from app.schemas.agents import AgentProfile

    profile = AgentProfile(
        id="1", name="Bot", handle="bot", agent_type="external",
        model_provider="x", model_name="y", specialization="programmer",
        skills=[], karma=0, projects_created=0, code_commits=0, reviews_done=0,
        last_heartbeat=None, is_active=True, created_at="2026-01-01",
        fork_count=5, is_hosted=True,
    )
    assert profile.fork_count == 5
    assert profile.is_hosted is True


def test_agent_profile_defaults():
    from app.schemas.agents import AgentProfile

    profile = AgentProfile(
        id="1", name="Bot", handle="bot", agent_type="external",
        model_provider="x", model_name="y", specialization="programmer",
        skills=[], karma=0, projects_created=0, code_commits=0, reviews_done=0,
        last_heartbeat=None, is_active=True, created_at="2026-01-01",
    )
    assert profile.fork_count == 0
    assert profile.is_hosted is False


# ── Cron task schemas ──


def test_cron_create_request_validation():
    from app.schemas.hosted_agents import CronTaskCreateRequest

    req = CronTaskCreateRequest(
        name="Test", cron_expression="0 9 * * *", task_prompt="Do work",
    )
    assert req.enabled is True
    assert req.auto_start is True
    assert req.max_runs is None


def test_cron_update_request_partial():
    from app.schemas.hosted_agents import CronTaskUpdateRequest

    req = CronTaskUpdateRequest(enabled=False)
    data = req.model_dump(exclude_unset=True)
    assert data == {"enabled": False}


# ── Forkable agent item ──


def test_forkable_agent_item():
    from app.schemas.hosted_agents import ForkableAgentItem

    item = ForkableAgentItem(
        id="1", agent_id="2", agent_name="Bot", agent_handle="bot",
        model="test/m:free", specialization="programmer",
        skills=["python"], description="A bot", fork_count=3,
    )
    assert item.fork_count == 3
    assert item.skills == ["python"]
    assert item.forked_from_agent_name is None

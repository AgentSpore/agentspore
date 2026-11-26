"""Tests for the runner: attempt lifecycle on a fake stand, budget stop, summary rendering."""

import argparse
import asyncio
import subprocess

import httpx
import onboarding_eval as runner
import pytest


class FakeStand:
    origin = "http://127.0.0.1:1"

    def __init__(self, start_error=None, close_error=None):
        self.start_error, self.close_error, self.closed = start_error, close_error, False

    async def start(self):
        if self.start_error:
            raise self.start_error

    async def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


def attempt_result(model="m", cost=0.1, outcome="success"):
    return {
        "model": model, "outcome": outcome, "ended_by": "final_message", "turns": 3, "cost": cost,
        "first_heartbeat": {"turns": 3, "total_tokens": 10, "seconds": 2.0} if outcome == "success" else None,
        "errors": [{"method": "POST", "path": "/x", "status": 422, "body": ""}],
        "off_origin": 0, "prod_url_rewritten": 1,
    }  # fmt: skip


@pytest.fixture
def options(tmp_path):
    key = tmp_path / "key"
    key.write_text("k")
    return argparse.Namespace(
        source=tmp_path, backend_python=tmp_path, docker_host="", recovery_dir=tmp_path, key_file=key,
        out_dir=tmp_path / "out", models=["a", "b"], attempts=2, budget=1.5, reserve=0.15,
    )  # fmt: skip


def one(options, stand, monkeypatch, result=None):
    async def fake_run(model, origin, chat_fn, http):
        return result or attempt_result(model)

    monkeypatch.setattr(runner, "run_attempt", fake_run)
    return asyncio.run(runner.one_attempt(options, "m", httpx.AsyncClient(), lambda *a: stand))


def test_cleanup_error_keeps_result(options, monkeypatch):
    for error in (RuntimeError("baseline"), OSError("rmtree")):
        stand = FakeStand(close_error=error)
        got = one(options, stand, monkeypatch)
        assert got["outcome"] == "success" and str(error) in str(got["cleanup_error"]) and stand.closed


def test_docker_timeout_on_start_is_recorded_as_stand_error(options, monkeypatch):
    stand = FakeStand(start_error=subprocess.TimeoutExpired("docker", 120))
    got = one(options, stand, monkeypatch)
    assert got["ended_by"] == "stand_error" and stand.closed


def test_start_failure_is_recorded_as_infra_and_stand_closed(options, monkeypatch):
    stand = FakeStand(start_error=RuntimeError("Fresh owned database schema absent"))
    got = one(options, stand, monkeypatch)
    assert got["outcome"] == "infra_error" and got["ended_by"] == "stand_error"
    assert "schema absent" in got["infra_detail"] and stand.closed
    assert runner.render_summary([got], 0.0, None)


def test_budget_stop_exits_both_loops(options, monkeypatch):
    calls = []

    async def fake_one(opts, model, llm):
        calls.append(model)
        return attempt_result(model, cost=1.0)

    async def fake_credits(llm):
        return 0.0

    monkeypatch.setattr(runner, "one_attempt", fake_one)
    monkeypatch.setattr(runner, "credits", fake_credits)
    options.out_dir.mkdir()
    attempts = []
    stopped = asyncio.run(runner.run_all(options, httpx.AsyncClient(), attempts, 0.0))
    assert calls == ["a"] and "budget" in str(stopped) and len(attempts) == 1


def test_summary_is_written_even_when_an_attempt_crashes(options, monkeypatch):
    async def boom(opts, model, llm):
        raise ValueError("unexpected")

    async def fake_credits(llm):
        return 0.0

    monkeypatch.setattr(runner, "one_attempt", boom)
    monkeypatch.setattr(runner, "credits", fake_credits)
    with pytest.raises(ValueError):
        asyncio.run(runner.main_async(options))
    assert "Total spend" in (options.out_dir / "summary.md").read_text()


def test_render_summary_table_spend_and_errors():
    text = runner.render_summary(
        [attempt_result(), attempt_result(outcome="agent_failure"), attempt_result(outcome="infra_error")],
        0.5, "budget hit",
    )  # fmt: skip
    assert "| m | 1/2 | 3 | 10 | 2 | 1 | 0.300 |" in text
    assert "Total spend: 0.500 USD (stopped: budget hit)" in text
    assert "POST /x -> 422: 3" in text and "prod_url_rewritten (informational): 3" in text


@pytest.mark.parametrize("error", [ValueError("unexpected"), KeyboardInterrupt()])
def test_abort_marker_and_spend_fallback_label_in_summary(options, monkeypatch, error):
    async def boom(opts, model, llm):
        raise error

    state = {"n": 0}

    async def flaky_credits(llm):
        state["n"] += 1
        if state["n"] > 1:
            raise httpx.ConnectError("down")
        return 0.0

    monkeypatch.setattr(runner, "one_attempt", boom)
    monkeypatch.setattr(runner, "credits", flaky_credits)
    with pytest.raises(type(error)):
        asyncio.run(runner.main_async(options))
    text = (options.out_dir / "summary.md").read_text()
    assert f"aborted: {type(error).__name__}" in text
    assert "Total spend (sum of measured attempt costs; /credits unavailable)" in text


def test_budget_check_survives_credits_failure(options, monkeypatch):
    async def fake_one(opts, model, llm):
        return attempt_result(model, cost=0.1)

    async def down(llm):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(runner, "one_attempt", fake_one)
    monkeypatch.setattr(runner, "credits", down)
    options.out_dir.mkdir()
    attempts = []
    assert asyncio.run(runner.run_all(options, httpx.AsyncClient(), attempts, 0.0)) is None
    assert len(attempts) == 4

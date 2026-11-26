"""Tests for onboarding metrics: origin guard, step detection, masking, aggregation."""

import onboarding_metrics as ev
import pytest

ORIGIN = "http://127.0.0.1:9000"


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        (ORIGIN + "/skill.md", True),
        ("/api/v1/agents/register", True),
        ("https://agentspore.com/skill.md", False),
        ("http://127.0.0.1:9001/skill.md", False),
        ("http://127.0.0.1:9000@evil.example/x", False),
        ("https://127.0.0.1:9000/x", False),
    ],
)
def test_origin_guard(url, allowed):
    assert ev.in_origin(url, ORIGIN) is allowed


def call(method, path, status, authed=False):
    return ev.Call(method=method, path=path, status=status, authed=authed)


def test_steps_full_happy_path():
    steps = ev.detect_steps(
        [
            call("GET", "/skill.md", 200),
            call("POST", "/api/v1/agents/register", 201),
            call("POST", "/api/v1/agents/heartbeat", 200, authed=True),
            call("POST", "/api/v1/agents/projects", 422, authed=True),
        ]
    )
    assert steps == dict.fromkeys(ev.STEPS, True)


def test_steps_failed_calls_do_not_count():
    steps = ev.detect_steps(
        [
            call("GET", "/skill.md", 404),
            call("POST", "/api/v1/agents/register", 409),
            call("POST", "/api/v1/agents/heartbeat", 401, authed=True),
        ]
    )
    assert not any(steps.values())


def test_heartbeat_without_register_still_counts_only_on_2xx():
    steps = ev.detect_steps([call("POST", "/api/v1/agents/heartbeat", 200, authed=True)])
    assert steps["first_heartbeat_ok"] and not steps["registered"]


def test_mask_hides_issued_and_openrouter_keys():
    text = "key af_" + "a" * 40 + " and sk-or-v1-" + "b" * 40 + " and own " + "plainsecretvalue"
    masked = ev.mask(text, ["plainsecretvalue"])
    assert "a" * 40 not in masked and "b" * 40 not in masked and "plainsecretvalue" not in masked
    assert "af_***" in masked


def attempt(model, outcome, turns=5, tokens=1000, cost=0.1, seconds=10.0):
    heartbeat = {"turns": turns, "total_tokens": tokens, "seconds": seconds} if outcome == "success" else None
    return {"model": model, "outcome": outcome, "first_heartbeat": heartbeat, "cost": cost}


def test_summary_excludes_infra_from_rate_and_takes_medians():
    rows = [
        attempt("m", "success", turns=4),
        attempt("m", "success", turns=10),
        attempt("m", "agent_failure"),
        attempt("m", "infra_error"),
    ]
    got = ev.summarize(rows)["m"]
    assert (got["success"], got["counted"], got["infra_error"]) == (2, 3, 1)
    assert got["median_turns"] == 7 and got["spend"] == pytest.approx(0.4)


def test_summary_no_success_has_no_median():
    got = ev.summarize([attempt("m", "agent_failure")])["m"]
    assert got["median_turns"] is None and got["success"] == 0


def test_outcome_classification():
    steps_ok = {**dict.fromkeys(ev.STEPS, False), "first_heartbeat_ok": True}
    steps_no = dict.fromkeys(ev.STEPS, False)
    assert ev.classify(steps_ok, infra=True) == "success"
    assert ev.classify(steps_no, infra=True) == "infra_error"
    assert ev.classify(steps_no, infra=False) == "agent_failure"


def test_key_used_needs_register_first_and_an_auth_path():
    public = [call("GET", "/skill.md", 200, authed=True)]
    assert not ev.detect_steps(public)["key_used"]
    before = [call("GET", "/api/v1/agents/tasks", 200, authed=True)]
    assert not ev.detect_steps(before)["key_used"]
    after = [call("POST", "/api/v1/agents/register", 201), call("GET", "/api/v1/agents/tasks", 200, authed=True)]
    assert ev.detect_steps(after)["key_used"]
    rejected = [call("POST", "/api/v1/agents/register", 201), call("GET", "/api/v1/agents/tasks", 401, authed=True)]
    assert not ev.detect_steps(rejected)["key_used"]


@pytest.mark.parametrize("url", ["http://127.0.0.1:abc/x", "http://[::1/x"])
def test_malformed_urls_are_outside_origin_and_not_rewritten(url):
    assert ev.in_origin(url, ORIGIN) is False
    assert ev.rewrite_prod_url(url, ORIGIN) == url

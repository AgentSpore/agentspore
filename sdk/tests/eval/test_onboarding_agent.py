"""Tests for the agent loop with a fake model and a mocked stand."""

import asyncio
import json

import httpx
import onboarding_agent as ev
import pytest

ORIGIN = "http://127.0.0.1:9000"


class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)

    async def __call__(self, payload):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def tool_reply(url, method="GET", body=None):
    args = {"method": method, "url": url, **({"body": body} if body else {})}
    call_ = {"id": "c1", "type": "function", "function": {"name": "http_request", "arguments": json.dumps(args)}}
    return {
        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call_]}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.01},
    }


def final_reply(text="done"):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 5, "cost": 0.01},
    }


def stand_transport():
    def handler(request):
        if request.url.path == "/skill.md":
            return httpx.Response(200, text="# skill")
        if request.url.path == "/api/v1/agents/register":
            return httpx.Response(201, json={"agent_id": "x", "api_key": "af_" + "k" * 40})
        if request.url.path == "/leak":
            return httpx.Response(400, text="x" * 280 + " af_" + "k" * 40)
        return httpx.Response(404, json={"detail": "nope"})

    return httpx.MockTransport(handler)


def run(chat, **kw):
    async def go():
        async with httpx.AsyncClient(transport=stand_transport()) as http:
            return await ev.run_attempt("m", ORIGIN, chat, http, **kw)

    return asyncio.run(go())


def test_loop_counts_off_origin_and_steps_and_masks_key():
    chat = FakeChat(
        [
            tool_reply("https://evil.example/skill.md"),
            tool_reply(ORIGIN + "/skill.md"),
            tool_reply(ORIGIN + "/api/v1/agents/register", "POST", {"name": "a"}),
            final_reply(),
        ]
    )
    result = run(chat)
    assert result["off_origin"] == 1 and result["turns"] == 4
    assert result["steps"]["skill_fetched"] and result["steps"]["registered"]
    assert result["outcome"] == "agent_failure" and result["ended_by"] == "final_message"
    assert "k" * 40 not in json.dumps(result)
    assert result["cost"] == pytest.approx(0.04)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://agentspore.com/skill.md", "/skill.md"),
        ("https://www.agentspore.com/api/v1/agents/register?x=1", "/api/v1/agents/register?x=1"),
        ("http://agentspore.com/heartbeat.md", "/heartbeat.md"),
    ],
)
def test_prod_urls_rewritten_to_stand_and_counted(url, expected):
    assert ev.rewrite_prod_url(url, ORIGIN) == ORIGIN + expected


def test_rewrite_leaves_other_hosts_alone():
    for url in ("https://evil.example/skill.md", "https://agentspore.com.evil.example/x", ORIGIN + "/skill.md", "/x"):
        assert ev.rewrite_prod_url(url, ORIGIN) == url


def test_loop_rewrites_prod_url_and_reaches_skill_step():
    result = run(FakeChat([tool_reply("https://agentspore.com/skill.md"), final_reply()]))
    assert result["prod_url_rewritten"] == 1 and result["off_origin"] == 0
    assert result["steps"]["skill_fetched"]


def test_loop_turn_ceiling():
    chat = FakeChat([tool_reply(ORIGIN + "/skill.md") for _ in range(3)])
    result = run(chat, max_turns=3)
    assert result["ended_by"] == "turn_ceiling" and result["turns"] == 3


def test_loop_infra_error_recorded_not_agent_failure():
    result = run(FakeChat([tool_reply(ORIGIN + "/skill.md"), ev.InfraError("503 x5")]))
    assert result["outcome"] == "infra_error" and "503" in result["infra_detail"]


GOOD = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}


def test_chat_retries_transport_and_5xx_then_gives_up():
    seen = []

    def handler(request):
        seen.append(1)
        return httpx.Response(503) if len(seen) < 3 else httpx.Response(200, json=GOOD)

    async def go(retries):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await ev.chat(http, {}, retries=retries, sleep=lambda _s: asyncio.sleep(0))

    assert asyncio.run(go(3)) == GOOD
    seen.clear()
    with pytest.raises(ev.InfraError):
        asyncio.run(go(1))


@pytest.mark.parametrize("reply", [{"choices": []}, {}, {"choices": None}, []])
def test_chat_treats_empty_or_malformed_reply_as_retryable_then_infra(reply):
    def handler(request):
        return httpx.Response(200, json=reply)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await ev.chat(http, {}, retries=1, sleep=lambda _s: asyncio.sleep(0))

    with pytest.raises(ev.InfraError):
        asyncio.run(go())


@pytest.mark.parametrize(
    "args",
    [
        {"method": "GET", "url": "http://127.0.0.1:abc/x"},
        {"method": "GET", "url": "http://[::1/x"},
        {"method": "POST", "url": "/api/v1/x", "body": 5},
        {"method": "GET", "url": "/api/v1/x", "headers": ["X-API-Key", "v"]},
        {"method": "GET", "url": 7},
        {"method": None, "url": "/x"},
        ["not", "an", "object"],
    ],
)
def test_tool_never_crashes_on_malformed_arguments(args):
    async def go():
        async with httpx.AsyncClient(transport=stand_transport()) as http:
            return await ev.Attempt(ORIGIN, http).tool(json.dumps(args))

    assert "error" in json.loads(asyncio.run(go()))


def test_error_body_is_masked_before_truncation():
    chat = FakeChat([tool_reply(ORIGIN + "/leak"), final_reply()])
    result = run(chat)
    assert "af_k" not in json.dumps(result["errors"])


@pytest.mark.parametrize(
    "reply", [{"choices": [{}]}, {"choices": ["x"]}, {"choices": [{"message": "hi"}]}, {"choices": {"a": 1}}]
)
def test_chat_requires_a_message_object(reply):
    def handler(request):
        return httpx.Response(200, json=reply)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await ev.chat(http, {}, retries=0, sleep=lambda _s: asyncio.sleep(0))

    with pytest.raises(ev.InfraError):
        asyncio.run(go())


def test_tool_call_without_function_gets_error_reply_not_keyerror():
    broken = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]}}]}
    result = run(FakeChat([broken, final_reply()]))
    tool_msgs = [m for m in result["transcript"] if m.get("role") == "tool"]
    assert result["turns"] == 2 and "invalid arguments" in tool_msgs[0]["content"]


def test_transcript_body_is_masked_before_truncation(monkeypatch):
    monkeypatch.setattr(ev, "BODY_LIMIT", 290)
    result = run(FakeChat([tool_reply(ORIGIN + "/leak"), final_reply()]))
    assert "af_k" not in json.dumps(result["transcript"])


def test_model_receives_its_key_but_saved_result_does_not():
    args = {"method": "POST", "url": ORIGIN + "/api/v1/agents/register", "body": {"name": "a"}}

    async def go():
        async with httpx.AsyncClient(transport=stand_transport()) as http:
            return await ev.Attempt(ORIGIN, http).tool(json.dumps(args))

    assert "af_" + "k" * 40 in json.loads(asyncio.run(go()))["body"]
    result = run(FakeChat([tool_reply(ORIGIN + "/api/v1/agents/register", "POST", {"name": "a"}), final_reply()]))
    assert "af_k" not in json.dumps(result)


def test_mask_hides_a_key_cut_short_by_truncation():
    assert "af_kkk" not in ev.mask('{"body": "x af_kkkkk"}', [])

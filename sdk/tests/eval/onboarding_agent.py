"""One agent attempt: origin-restricted tool-calling loop over OpenRouter chat completions."""

import asyncio
import json
import time
from collections import Counter
from typing import Any

import httpx
from onboarding_metrics import SECRET, Call, classify, detect_steps, in_origin, mask, rewrite_prod_url

OPENROUTER = "https://openrouter.ai/api/v1"
MAX_TURNS = 30
ATTEMPT_SECONDS = 600
MAX_TOKENS = 8192
BODY_LIMIT = 60000
SYSTEM = (
    "You are an autonomous software agent. Your operator wants you to join the platform described at "
    "{origin}/skill.md and get to work. Do it without asking the human anything."
)
TOOL = {
    "type": "function",
    "function": {
        "name": "http_request",
        "description": "Send an HTTP request. Only the platform origin {origin} is reachable.",
        "parameters": {
            "type": "object",
            "properties": {
                "method": {"type": "string"},
                "url": {"type": "string"},
                "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                "body": {"description": "JSON object or raw string request body"},
            },
            "required": ["method", "url"],
        },
    },
}


class InfraError(Exception):
    """Model transport failed after bounded retries; says nothing about the agent."""


async def chat(http: httpx.AsyncClient, payload: dict[str, Any], retries: int = 4, sleep: Any = asyncio.sleep) -> dict:
    """POST one completion; transport errors, 429, 5xx and in-body errors retry with backoff."""
    failure = "no attempt"
    for n in range(retries + 1):
        try:
            response = await http.post(OPENROUTER + "/chat/completions", json=payload)
            if response.status_code == 429 or response.status_code >= 500:
                failure = f"http {response.status_code}"
            elif response.status_code >= 400:
                raise InfraError(f"request rejected: http {response.status_code}: {response.text[:200]}")
            else:
                data = response.json()
                if not isinstance(data, dict):
                    failure = "reply is not an object"
                elif "error" in data:
                    failure = "error body: " + str(data["error"])[:200]
                elif not _has_message(data):
                    failure = "reply without a message"
                else:
                    return data
        except (httpx.TransportError, ValueError) as error:
            failure = type(error).__name__
        if n < retries:
            await sleep(2**n)
    raise InfraError(f"{failure} after {retries + 1} tries")


def _has_message(data: dict[str, Any]) -> bool:
    choices = data.get("choices")
    return isinstance(choices, list) and bool(choices) and isinstance(choices[0], dict) and isinstance(choices[0].get("message"), dict)


def _tool_args(raw: str | None) -> tuple[str, str, dict[str, str], Any] | str:
    """Validate model-supplied tool arguments; a string result is the error text for the agent."""
    if not isinstance(raw, str):
        return "invalid arguments: missing"
    try:
        args = json.loads(raw)
    except ValueError:
        return "invalid arguments: not JSON"
    if not isinstance(args, dict) or not isinstance(args.get("method"), str) or not isinstance(args.get("url"), str):
        return "invalid arguments: method and url must be strings"
    headers, body = args.get("headers") or {}, args.get("body")
    if not isinstance(headers, dict):
        return "invalid arguments: headers must be an object"
    if body is not None and not isinstance(body, (dict, list, str)):
        return "invalid arguments: body must be an object, array or string"
    return args["method"].upper(), args["url"], {str(k): str(v) for k, v in headers.items()}, body


class Attempt:
    """State of one run: HTTP transcript, counters and learned secrets for one agent."""

    def __init__(self, origin: str, http: httpx.AsyncClient):
        self.origin, self.http = origin, http
        self.calls: list[Call] = []
        self.statuses: Counter[str] = Counter()
        self.errors: list[dict[str, Any]] = []
        self.off_origin = 0
        self.prod_url_rewritten = 0
        self.secrets: list[str] = []

    async def tool(self, raw: str | None) -> str:
        parsed = _tool_args(raw)
        if isinstance(parsed, str):
            return json.dumps({"error": parsed})
        method, url, headers, body = parsed
        rewritten = rewrite_prod_url(url, self.origin)
        self.prod_url_rewritten += rewritten != url
        url = rewritten
        if not in_origin(url, self.origin):
            self.off_origin += 1
            return json.dumps({"error": f"blocked: only {self.origin} is reachable from this sandbox"})
        kwargs: dict[str, Any] = {"json": body} if isinstance(body, (dict, list)) else {"content": body}
        try:
            r = await self.http.request(
                method, self.origin + url if url.startswith("/") else url, headers=headers, **kwargs
            )
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
            return json.dumps({"error": type(error).__name__})
        return self._record(method, r, any(k.lower() in ("x-api-key", "authorization") for k in headers))

    def _record(self, method: str, r: httpx.Response, authed: bool) -> str:
        path = r.request.url.path
        self.calls.append(Call(method, path, r.status_code, authed))
        self.statuses[str(r.status_code)] += 1
        match = SECRET.search(r.text)
        if match and match.group(0) not in self.secrets:
            self.secrets.append(match.group(0))
        if r.status_code >= 400:
            body = mask(r.text, self.secrets)[:300]
            self.errors.append({"method": method, "path": path, "status": r.status_code, "body": body})
        # The model must see its real key to authenticate; saved output is masked in _finish.
        return json.dumps({"status": r.status_code, "body": r.text[:BODY_LIMIT]})


def _usage_totals(usage: Counter, reply: dict[str, Any]) -> None:
    raw = reply.get("usage") or {}
    for key, value in raw.items():
        if isinstance(value, (int, float)):
            usage[key] += value
    usage["reasoning"] += (raw.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0


async def run_attempt(
    model: str, origin: str, chat_fn: Any, http: httpx.AsyncClient, max_turns: int = MAX_TURNS
) -> dict:
    """Drive one agent to a ceiling, a final message, or an infra failure; return its masked metrics."""
    state, started, usage = Attempt(origin, http), time.monotonic(), Counter()
    tool = json.loads(json.dumps(TOOL).replace("{origin}", origin))
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM.format(origin=origin)},
        {"role": "user", "content": f"Begin. The platform is at {origin}."},
    ]
    result: dict[str, Any] = {"model": model, "ended_by": "turn_ceiling", "infra_detail": None, "first_heartbeat": None}
    final, turns = None, 0
    while turns < max_turns:
        if time.monotonic() - started > ATTEMPT_SECONDS:
            result["ended_by"] = "time_ceiling"
            break
        payload = {"model": model, "messages": messages, "tools": [tool], "max_tokens": MAX_TOKENS}
        try:
            reply = await chat_fn({**payload, "usage": {"include": True}})
        except InfraError as error:
            result.update(ended_by="infra_error", infra_detail=str(error))
            break
        turns += 1
        _usage_totals(usage, reply)
        message = reply["choices"][0]["message"]
        messages.append(message)
        if not message.get("tool_calls"):
            result["ended_by"], final = "final_message", message.get("content")
            break
        for tc in message["tool_calls"]:
            function = tc.get("function")
            output = await state.tool((function or {}).get("arguments") if isinstance(function, dict) else None)
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": output})
        if not result["first_heartbeat"] and detect_steps(state.calls)["first_heartbeat_ok"]:
            total = usage["prompt_tokens"] + usage["completion_tokens"]
            result["first_heartbeat"] = {"turns": turns, "total_tokens": total, "seconds": time.monotonic() - started}
    return _finish(result, state, usage, messages, (final, turns, started))


def _finish(result: dict, state: Attempt, usage: Counter, messages: list, run: tuple) -> dict:
    final, turns, started = run
    steps = detect_steps(state.calls)
    result.update(
        outcome=classify(steps, result["ended_by"] == "infra_error"),
        steps=steps,
        turns=turns,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        reasoning_tokens=usage["reasoning"],
        cost=usage["cost"],
        wall_seconds=time.monotonic() - started,
        statuses=dict(state.statuses),
        errors=state.errors,
        off_origin=state.off_origin,
        prod_url_rewritten=state.prod_url_rewritten,
        final_message=final or "",
        transcript=messages,
    )
    return json.loads(mask(json.dumps(result, ensure_ascii=False, default=str), state.secrets))
